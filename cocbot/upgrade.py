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

from .adbtool import screenshot, swipe, tap
from .config import BUILDINGS, CFG, NEVER_UPGRADE, TEMPLATES, imread, priority, save_debug
from .menu import MENU_DX, read_rows

DESELECT = (1590, 340)  # 地图右侧空地/树林，点了没副作用（5 本后右边 y≈395~500 多了村民委托浮窗，别点到）
X_SCALES = (0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5)


class Danger(Exception):
    """屏幕上出现了可能花宝石的窗口。"""


class Locked(Danger):
    """升级条件没满足（"升级尚未解锁"窗口）。"""


class Warned(Danger):
    """"在您升级之前……"：游戏建议先升进攻方面再升大本营。"""


# 游戏建议先升进攻时，这些建筑排到最前面（兵种研究在 lab.py 里本来就优先主力兵）
OFFENSE = {"camp", "cc", "lab", "barracks"}


def _red_count(hsv):
    h = hsv[..., 0]
    return int((((h < 10) | (h > 170)) & (hsv[..., 1] > 100) & (hsv[..., 2] > 150)).sum())


def _white_count(hsv):
    return int(((hsv[..., 1] < 40) & (hsv[..., 2] > 200)).sum())


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


_failed = {}               # 升不了的 (建筑, 资源, 价格) → 到什么时候之前不再试
FAIL_COOLDOWN = 2 * 3600


def try_upgrade(find_all, find_all_scaled, tpl, log, skip_gold=False, allow_walls=False, offense_first=False):
    """按升级优先级升一个现有建筑，成功返回 True。
    skip_gold：金币留着升大本营，只升花圣水的；allow_walls：允许升城墙（一般是金币满了）。"""
    now = time.time()
    banned = {k for k, until in _failed.items() if until > now}  # 最近升不了的建筑，先跳过
    for _ in range(3):
        try:
            r = _try_upgrade(find_all, tpl, log, skip_gold, allow_walls, banned, offense_first)
        except Locked:
            close_windows(find_all, find_all_scaled, tpl, log)
            return False
        except Danger:
            log("出现可能花宝石的窗口，放弃本次升级")
            close_windows(find_all, find_all_scaled, tpl, log)
            return False
        if r is True or r is False:
            return r
        banned.add(r)  # 返回的是升不了的那个 (建筑, 资源, 价格)
        _failed[r] = time.time() + FAIL_COOLDOWN
        log(f"{BUILDINGS.get(r[0], '这个建筑')}暂时升不了（比如部落城堡还是废墟要先重建），2 小时内先不试")
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


def walls_first():
    """按优先级升级、并且城墙排在第一位：城墙不受"金币满了才升""金币留着升大本营"的限制。"""
    return CFG["upgrade_rule"] == "priority" and priority()[:1] == ["wall"]


def _usable(row, skip_gold, allow_walls, banned):
    if row["new"] or row["red"] or row["cur"] not in ("gold", "elixir") or row["key"] in NEVER_UPGRADE:
        return False
    if (row["key"], row["cur"], row["price"]) in banned:
        return False
    if row["key"] == "wall" and walls_first():
        return True
    if skip_gold and row["cur"] == "gold":
        return False
    return allow_walls or row["key"] != "wall"


def close_menu(find_all, tpl):
    """点完菜单里的某一行后，再点一下工人图标收起菜单。建筑仍保持选中，菜单不会再挡住底部的建筑名字。"""
    icons = find_all(_safe_shot(find_all, tpl), tpl["builder_icon"], 0.8)
    if icons:
        tap(*icons[0])
        time.sleep(1)


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


def _sort_key(rule, rank, offense_first=False):
    """按升级规则给菜单行排序，小的先升。
    cheap：先升最便宜的——同一种建筑等级越低越便宜，这样低级的建筑会先被补上来；城墙永远排最后；
    priority：按优先级列表，同一档里便宜的先升。
    offense_first：游戏建议先升进攻时，OFFENSE 里的建筑排在所有建筑前面。"""
    def key(r):
        price = r["price"] if r["price"] is not None else 10 ** 9
        first = offense_first and r["key"] in OFFENSE
        if rule == "priority":
            return (not first, rank(r["key"]), price)
        return (not first, r["key"] == "wall", price, rank(r["key"]))
    return key


def _try_upgrade(find_all, tpl, log, skip_gold, allow_walls, banned, offense_first=False):
    order = priority()
    rank = lambda k: order.index(k) if k in order else order.index("other")
    key = _sort_key(CFG["upgrade_rule"], rank, offense_first)
    ident = lambda r: (r["key"], r["cur"], r["price"])

    # 第一遍：整个菜单看一遍，按升级规则挑最该升的买得起的建筑
    mx = _open_menu(find_all, tpl)
    if mx is None:
        log("工人菜单没打开")
        return False
    best = None
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if _usable(r, skip_gold, allow_walls, banned) and (best is None or key(r) < key(best)):
                best = r
    tap(*DESELECT)
    time.sleep(1)
    if not best:
        log("没有买得起的升级" + ("（金币留着升大本营）" if skip_gold else ""))
        return False
    name = BUILDINGS.get(best["key"], "建筑")
    price = f"{best['price']} " if best["price"] else ""
    log(f"升级：{name}（{price}{'金币' if best['cur'] == 'gold' else '圣水'}）")
    target = ident(best)

    # 第二遍：重新打开菜单，翻到这个建筑点它
    mx = _open_menu(find_all, tpl)
    if mx is None:
        return False
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if ident(r) == target and _usable(r, skip_gold, allow_walls, banned):
                tap(mx + 75, r["y"])
                time.sleep(2)
                res = _press_upgrade(find_all, tpl, log, name)
                return target if res == "skip" else res
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


def green_confirm(find_all, tpl, img):
    """确认窗口里绿色的"确认"按钮（花金币/圣水的那个），返回中心或 None。
    有道具时旁边还会多一个灰色的"确认"（用建筑之锤/战斗之锤），那个是道具，永远不点。"""
    best = None
    for x, y in find_all(img, tpl["confirm_label"], 0.8):
        hsv = cv2.cvtColor(img[y - 10:y + 70, x - 110:x + 110], cv2.COLOR_BGR2HSV)
        green = ((hsv[..., 0] >= 30) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 120) & (hsv[..., 2] > 180)).mean()
        if green > 0.15 and (best is None or x > best[0]):
            best = (x, y)
    return best


def check_confirm(find_all, tpl, log, img, name):
    """检查确认窗口能不能点：要有绿色确认按钮、图标不是宝石、价格是白字。能点返回按钮中心，不能点抛 Danger。"""
    confirm = green_confirm(find_all, tpl, img)
    if not confirm:
        log(f"{name}没出现确认窗口（截图存在 debug\\{name}_没出现确认窗口.png）")
        save_debug(img, f"{name}_没出现确认窗口")
        raise Danger  # 交给统一的关窗口逻辑
    cx, cy = confirm
    if _icon_is_gem(img, cx, cy):
        log("确认按钮要花宝石，取消")
        raise Danger
    cost = cv2.cvtColor(img[cy + 28:cy + 62, cx - 80:cx + 40], cv2.COLOR_BGR2HSV)
    if _white_count(cost) < 300:  # 买得起时价格是白字，红字/看不清都不点
        log(f"{name}的价格不是白色（资源不够），取消")
        raise Danger
    return cx, cy


def _press_upgrade(find_all, tpl, log, name):
    """建筑已选中：点"升级"→检查→点"确认"。返回 True / "skip"（没有升级按钮）/ "locked"（升级条件没满足）。"""
    img = _safe_shot(find_all, tpl)
    btn = _find_upgrade_btn(img, tpl)
    if not btn:
        log(f"没找到升级按钮，换下一项（截图存在 debug\没找到升级按钮_{name}.png）")
        save_debug(img, f"没找到升级按钮_{name}")
        tap(*DESELECT)
        time.sleep(0.5)
        tap(*DESELECT)  # 第一下可能只是关掉了菜单
        return "skip"
    tap(btn[0], btn[1])
    time.sleep(1.5)

    cx, cy = check_confirm(find_all, tpl, log, _safe_shot(find_all, tpl), name)
    tap(cx + random.randint(-20, 20), cy + 30 + random.randint(-5, 5))

    # 弹窗有动画，分几次检查
    for _ in range(4):
        time.sleep(1)
        img = _safe_shot(find_all, tpl)
    if find_all(img, tpl["th_warn"], 0.8):
        # "在您升级之前……升级大本营后会匹配到更强的对手，建议先进行一些进攻方面的升级"：点取消（关窗口）
        log(f"游戏建议先升进攻再升{name}，这次取消")
        raise Warned
    if find_all(img, tpl["th_locked"], 0.8):
        # "升级尚未解锁：请先建造……"，一般是大本营要求先造齐新建筑
        log(f"{name}升级条件没满足：要先造齐新建筑（截图存在 debug\\{name}_升级条件.png）")
        save_debug(img, f"{name}_升级条件")
        raise Locked
    if find_all(img, tpl["confirm_label"], 0.8) or find_all(img, tpl["close_x"], 0.8):
        log(f"确认后窗口还在，当作失败处理（截图存在 debug\\{name}_确认后窗口还在.png）")
        save_debug(img, f"{name}_确认后窗口还在")
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
        for x, y in find_all_scaled(img[130:720, 120:1460], t, 0.75, (0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3)):
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
    """尝试升级大本营。返回 "done"（已开始升级）/ "poor"（金币不够）/ "busy"（正在升级中）/
    "locked"（要先造齐新建筑）/ "warned"（游戏建议先升进攻）/ "fail"（没找到、出了别的窗口等）。"""
    try:
        return _try_townhall(find_all, find_all_scaled, tpl, log)
    except Locked:
        close_windows(find_all, find_all_scaled, tpl, log)
        return "locked"
    except Warned:
        close_windows(find_all, find_all_scaled, tpl, log)
        return "warned"
    except Danger:
        log("出现可能花宝石的窗口，放弃升级大本营")
        close_windows(find_all, find_all_scaled, tpl, log)
        return "fail"


def _select_townhall_on_map(find_all, find_all_scaled, tpl):
    """在画面里找大本营：先按各等级模板，再按橙色屋顶，点开看名字确认。选中了返回截图，否则 None。"""
    img = _safe_shot(find_all, tpl)
    cands = _townhall_by_template(img, find_all_scaled)[:3] + _townhall_candidates(img)
    for x, y in cands:
        tap(x, y + 10)
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        if find_all(img, tpl["label_townhall"], 0.8):
            return img
        tap(*DESELECT)
        time.sleep(0.8)
    return None


def _focus_townhall_from_menu(find_all, tpl):
    """镜头挪偏了、画面里没有大本营时：在工人菜单里点"大本营"那一行，镜头会移过去对准大本营。
    点到了返回 True（正在升级时菜单里没有这一行，返回 False）。"""
    mx = _open_menu(find_all, tpl)
    if mx is None:
        return False
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            if r["key"] == "townhall":
                tap(mx + 75, r["y"])
                time.sleep(2)
                close_menu(find_all, tpl)
                return True
    tap(*DESELECT)
    return False


def _try_townhall(find_all, find_all_scaled, tpl, log):
    """升级大本营。"""
    img = _select_townhall_on_map(find_all, find_all_scaled, tpl)
    if img is None and _focus_townhall_from_menu(find_all, tpl):
        img = _safe_shot(find_all, tpl)
        if not find_all(img, tpl["label_townhall"], 0.8):  # 镜头移过去了但没选中，再在画面里找一次
            tap(*DESELECT)
            time.sleep(1)
            img = _select_townhall_on_map(find_all, find_all_scaled, tpl)
    if img is None:
        log("没找到大本营（截图存在 debug\\没找到大本营.png）")
        save_debug(_safe_shot(find_all, tpl), "没找到大本营")
        return "fail"
    btn = _find_upgrade_btn(img, tpl)
    if not btn:
        tap(*DESELECT)
        return "busy"
    if _price_is_red(img, *btn):
        tap(*DESELECT)
        return "poor"
    return "done" if _press_upgrade(find_all, tpl, log, "大本营") is True else "fail"


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


# 位置被挡住时，从游戏选的位置开始一格一格往外转圈挪，每挪一格就看一次能不能放下。
# 默认缩放下一格地砖在屏幕上约是：往右下一格 (+22, +16.5)，往左下一格 (-22, +16.5)
TILE = (22, 16.5)
SAFE_BOX = (170, 170, 1420, 720)    # 建筑只在这个范围里挪，避开四周的按钮
MAX_MOVES = 80


def _spiral(n):
    """以 (0, 0) 为起点的方形螺旋（地砖坐标），相邻两点只差一格，越往后离起点越远。"""
    i = j = 0
    di, dj = 1, 0
    run, pts = 1, []
    while len(pts) < n:
        for _ in range(2):
            for _ in range(run):
                i, j = i + di, j + dj
                pts.append((i, j))
            di, dj = -dj, di
        run += 1
    return pts[:n]


def _drag_to_free(find_all, tpl, cancel, log):
    """摆放模式下 ✓ 是灰的（位置不合法）：一格一格挪建筑，直到 ✓ 变绿。返回绿 ✓ 的位置或 None。"""
    x0, y0 = cancel[0] + 40, cancel[1] + 70  # 按住 ✗ 和 ✓ 中间偏下（1 格的炸弹中心约 +58，3 格的建筑约 +90）
    cx, cy = x0, y0
    moves = 0
    for i, j in _spiral(400):
        tx, ty = x0 + (i - j) * TILE[0], y0 + (i + j) * TILE[1]
        if not (SAFE_BOX[0] <= tx <= SAFE_BOX[2] and SAFE_BOX[1] <= ty <= SAFE_BOX[3]):
            continue
        img = _safe_shot(find_all, tpl)
        cur = _find_place(img, tpl, "place_cancel")
        if not cur:  # 已经不在摆放模式了（误点了别的东西），马上停手
            log("挪建筑时离开了摆放模式，停止")
            return None
        cx, cy = cur[0][0] + 40, cur[0][1] + 70
        swipe(cx, cy, tx, ty, 500)
        time.sleep(0.8)
        ok = _find_place(_safe_shot(find_all, tpl), tpl, "place_ok")
        moves += 1
        if ok:
            log(f"游戏选的位置被挡住了，挪了 {moves} 次找到能放的位置")
            return ok
        if moves >= MAX_MOVES:
            break
    log(f"挪了 {moves} 次还是放不下")
    return None


def _new_usable(row, skip_gold):
    """带绿色"新"字、花金币/圣水（或者免费，比如升本送的城墙）、买得起的行（建筑工人小屋要宝石，不造）。"""
    if not row["new"] or row["red"] or row["cur"] not in ("gold", "elixir", None) or row["key"] in NEVER_UPGRADE:
        return False
    return not (skip_gold and row["cur"] == "gold")


def _try_build_new(find_all, tpl, log, skip_gold, pick=0):
    # 菜单从头翻到尾，找第 pick 个能造的新建筑（新建筑常在菜单最下面）
    mx = _open_menu(find_all, tpl)
    if mx is None:
        return False
    seen, target = [], None
    for _, rows in _pages(find_all, tpl, mx):
        for r in rows:
            ident = (r["key"], r["cur"], r["price"])
            if not _new_usable(r, skip_gold) or ident in seen:
                continue
            if len(seen) == pick:
                target = r
                break
            seen.append(ident)
        if target:
            break
    if not target:
        tap(*DESELECT)
        return False
    tap(mx + 75, target["y"])
    time.sleep(2)

    # 打开商店，要造的建筑卡片是亮蓝色的，其他是灰色
    img = _safe_shot(find_all, tpl)
    card = _target_card(img)
    if not card:
        log("商店里没找到要造的建筑卡片（或者它要花宝石）")
        raise Danger  # 统一关窗口
    tap(*card)
    time.sleep(2)

    # 摆放：城墙、陷阱这类一次有好几个的，放下一个后游戏会自动拿出下一个，接着放
    built = 0
    for _ in range(10):
        img = _safe_shot(find_all, tpl)
        ok = _find_place(img, tpl, "place_ok")
        cancel = _find_place(img, tpl, "place_cancel")
        if not ok and cancel:  # ✓ 是灰的：游戏选的位置被挡住了，一格一格挪
            ok = _drag_to_free(find_all, tpl, cancel[0], log)
        if not ok:
            if not built:
                log("没进入摆放模式（截图存在 debug\\没进入摆放模式.png）")
                save_debug(img, "没进入摆放模式")
            cancel = _find_place(_safe_shot(find_all, tpl), tpl, "place_cancel")
            if cancel:
                tap(*cancel[0])
            if not built:
                raise Danger
            break
        tap(*ok[0])
        time.sleep(1.5)
        img = _safe_shot(find_all, tpl)
        same = lambda a: a and abs(a[0][0] - ok[0][0]) < 15 and abs(a[0][1] - ok[0][1]) < 15
        if same(_find_place(img, tpl, "place_ok")):  # 可能是动画还没播完，再等一下
            time.sleep(1.5)
            img = _safe_shot(find_all, tpl)
        if same(_find_place(img, tpl, "place_ok")):
            log("摆放位置不合法，取消")  # ✓ 还在原地：没放下去
            cancel = _find_place(img, tpl, "place_cancel")
            if cancel:
                tap(*cancel[0])
            break
        built += 1
        log("已建造一个新建筑")
        if not _find_place(img, tpl, "place_cancel"):  # 没有下一个了
            break
    tap(*DESELECT)
    return built > 0
