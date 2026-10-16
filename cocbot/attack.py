"""自动进攻：搜索对手 → 找资源建筑 → 从离它们最近的地图边缘分批下兵 → 等战斗结束 → 回营。

下兵思路：
1. 截图找对手的金矿/圣水收集器/储罐（金黄色、紫粉色的色块），离地图中心越远的越可能在城墙外、没人保护
2. 朝着这些资源建筑的方向，从屏幕外沿射线一步步往里试点；每试一下看兵种卡上的数字变没变，
   第一次变了就是能放兵的地图边缘（一定在红线外）
3. 兵分 2~3 批放在不同的资源点前面，野蛮人会先去打离自己最近的建筑
"""
import math
import random
import time

import cv2
import numpy as np

from .adbtool import screenshot, tap
from .config import CFG, TEMPLATES, imread, save_debug
from .scout import read_loot, read_number, worth_attacking

# 兵种卡槽（底部栏），从左数第 1 个起，间隔约 121 像素
SLOT_X0, SLOT_DX, SLOT_Y = 190, 121, 815
SLOTS = 7                    # 最多看几个卡槽（兵种 + 英雄），空槽会跳过
CARD_NUM = (742, 778)        # 卡片上 "x20" 数字所在的 y 范围
CENTER = (800, 420)          # 战斗画面里地图的大致中心
SCREEN_Y = (130, 712)        # 可以点的 y 范围（上面是信息栏，下面是兵种栏）
GROUPS = CFG["groups"]       # 兵分几批（在"挂机控制面板.bat"里改，下同）
BATTLE_TIMEOUT = 200         # 秒
MIN_GOLD = CFG["min_gold"]   # 金币没满时，对手金币达到这个数就打
MIN_ELIXIR = CFG["min_elixir"]  # 圣水没满时，对手圣水达到这个数就打；两种都不够就换下一个
MAX_SKIPS = CFG["max_skips"] # 最多换几个对手（每次 50 金币），换满了就直接打
# 战斗画面上的按钮，试点时绝对不能点到：(x1, y1, x2, y2)
NO_TAP = [
    (0, 620, 760, 720),      # 结束战斗/放弃、复制阵型、自动战斗
    (1300, 560, 1600, 720),  # 下一个、摧毁率面板
    (350, 0, 500, 95),       # 举报
    (0, 0, 320, 210),        # 左上对手信息、战利品
    (1320, 0, 1600, 150),    # 右上资源条
]


def _blocked(x, y):
    return any(x1 <= x <= x2 and y1 <= y <= y2 for x1, y1, x2, y2 in NO_TAP)


def _wait_for(find_all, tpl, timeout, interval=1.5):
    """等任意一个模板出现。tpl 可以是单个模板或模板列表。"""
    tpls = tpl if isinstance(tpl, list) else [tpl]
    end = time.time() + timeout
    while time.time() < end:
        img = screenshot()
        for t in tpls:
            pts = find_all(img, t, 0.8)
            if pts:
                return pts[0]
        time.sleep(interval)
    return None


# ---------- 找资源建筑 ----------

def loot_targets(img):
    """返回资源建筑的位置列表 [(x, y, 离中心距离)]，按距离从远到近排。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((h >= 18) & (h <= 30) & (s > 150) & (v > 180)).astype("uint8")
    # 去掉界面：左上战利品文字、右上资源条、底部兵种栏
    for x1, y1, x2, y2 in NO_TAP:
        mask[y1:y2, x1:x2] = 0
    mask[SCREEN_Y[1]:, :] = 0
    mask[:SCREEN_Y[0], :] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, lab, st, cents = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        area = st[i, cv2.CC_STAT_AREA]
        if area < 140:
            continue
        sel = lab == i
        mh, ms, mv = float(h[sel].mean()), float(s[sel].mean()), float(v[sel].mean())
        is_mine = 22 <= mh <= 27 and ms >= 210 and 180 <= mv <= 215 and area <= 600     # 金矿的金车
        is_pump = 26 <= mh <= 29 and 150 <= ms <= 172 and 195 <= mv <= 212 and area <= 300  # 圣水收集器顶上的金色
        is_storage = area >= 2000 and ms >= 175                                         # 储金罐（大块金色）
        # 土路（偏橙发灰、大块）、营火/头发/蜡烛（特别亮）都不满足上面任何一条
        if not (is_mine or is_pump or is_storage):
            continue
        x, y = cents[i]
        out.append((int(x), int(y), math.hypot(x - CENTER[0], (y - CENTER[1]) / 0.75)))
    return sorted(out, key=lambda t: -t[2])


def _pick_angles(img):
    """挑 GROUPS 个下兵方向：朝着最外面的几个资源建筑，彼此至少隔 35 度。"""
    angles = []
    for x, y, _ in loot_targets(img):
        a = math.degrees(math.atan2((y - CENTER[1]) / 0.75, x - CENTER[0])) % 360
        if all(min(abs(a - b), 360 - abs(a - b)) > 35 for b in angles):
            angles.append(a)
        if len(angles) == GROUPS:
            break
    if not angles:  # 一个资源建筑都没认出来，就随便挑一边
        base = random.choice([180, 225, 315, 0])
        angles = [base - 30, base, base + 30]
    return angles


# ---------- 找能放兵的边缘 ----------

def _card(img, slot):
    x = SLOT_X0 + SLOT_DX * slot
    return cv2.cvtColor(img[CARD_NUM[0]:CARD_NUM[1], x - 55:x + 55], cv2.COLOR_BGR2GRAY)


def _card_changed(before, after):
    return float(np.abs(before.astype(int) - after.astype(int)).mean()) > 6


def _card_empty(img, slot):
    """卡片用完后会变灰，或者整格空着。"""
    x = SLOT_X0 + SLOT_DX * slot
    hsv = cv2.cvtColor(img[760:870, x - 50:x + 50], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) < 45


def _find_edge(find_all, tpl, slot, angle):
    """沿 angle 方向从外往里试，返回第一个能放兵的点；找不到返回 None。
    试到能放的那一下会真的放出 1 个兵。"""
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad) * 0.75  # 等距视角，上下方向压扁一点
    before = _card(screenshot(), slot)
    for r in range(1000, 120, -28):
        x, y = int(CENTER[0] + dx * r), int(CENTER[1] + dy * r)
        if not (20 <= x <= 1580 and SCREEN_Y[0] <= y <= SCREEN_Y[1]) or _blocked(x, y):
            continue
        tap(x, y)
        time.sleep(0.35)
        img = screenshot()
        if find_all(img, tpl["redline_msg"], 0.8):  # 已经进红线了，这个方向不行
            return None
        if _card_changed(before, _card(img, slot)):
            return x, y
    return None


def _drop(x, y, n):
    """在 (x, y) 附近沿着边撒 n 个兵。"""
    for _ in range(n):
        px, py = x + random.randint(-30, 30), y + random.randint(-15, 15)
        if _blocked(px, py) or not (SCREEN_Y[0] <= py <= SCREEN_Y[1]):
            continue
        tap(px, py)
        time.sleep(random.uniform(0.06, 0.14))


def _in_battle(find_all, tpl, img):
    """还在战斗画面（没有结算按钮，底部兵种栏还在）。"""
    if find_all(img, tpl["btn_return"], 0.8) or find_all(img, tpl["btn_claim"], 0.8):
        return False
    return float(img[735:745, 0:1600].mean()) < 120  # 兵种栏顶上那条深色边


# ---------- 认兵种栏 ----------

_BAR = {p.stem: cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY) for p in sorted((TEMPLATES / "bar").glob("*.png"))}
BAR_NAMES = {"giant": "巨人", "breaker": "炸弹人", "archer": "弓箭手", "goblin": "哥布林", "barb": "野蛮人",
             "king": "野蛮人之王"}


def identify_slots(img):
    """兵种栏每个卡槽是什么兵，返回 {兵种: 卡槽号}。用灰度比对，卡片用完变灰也认得出。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    found = {}
    for slot in range(SLOTS):
        x = SLOT_X0 + SLOT_DX * slot
        roi = gray[765:860, max(0, x - 60):x + 60]
        best, kind = 0.6, None
        for k, t in _BAR.items():
            if t.shape[0] > roi.shape[0] or t.shape[1] > roi.shape[1]:
                continue
            v = float(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED).max())
            if v > best:
                best, kind = v, k
        if kind and kind not in found:
            found[kind] = slot
    return found


FUNNEL_BARBS = 4  # 开口时每侧放几个野蛮人


def _drop_all(find_all, tpl, slot, points, spread, per_tap=0.1, max_taps=150):
    """选中卡槽，在几个点之间轮流撒兵直到这张卡用完。spread 是撒开的半径（像素）。"""
    tap(SLOT_X0 + SLOT_DX * slot, SLOT_Y)
    time.sleep(0.3)
    for i in range(max_taps):
        x, y = points[i % len(points)]
        px, py = x + random.randint(-spread, spread), y + random.randint(-spread // 2, spread // 2)
        if not _blocked(px, py) and SCREEN_Y[0] <= py <= SCREEN_Y[1]:
            tap(px, py)
            time.sleep(per_tap)
        if i % 8 == 7:
            img = screenshot()
            if _card_empty(img, slot) or not _in_battle(find_all, tpl, img):
                return


def _edges(find_all, tpl, slot, angles):
    """用这张卡沿各个方向找能放兵的边缘（每找到一个点会放出 1 个兵）。"""
    tap(SLOT_X0 + SLOT_DX * slot, SLOT_Y)
    time.sleep(0.4)
    points = [p for p in (_find_edge(find_all, tpl, slot, a) for a in angles) if p]
    if not points:  # 朝资源的方向都不行，转一圈找
        for a in range(0, 360, 30):
            p = _find_edge(find_all, tpl, slot, a)
            if p:
                return [p]
    return points


def _deploy(find_all, tpl, log):
    """按兵种分工下兵：
    1. 巨人全部放在主攻点，吸引防御塔火力
    2. 等几秒，炸弹人跟在巨人后面去炸城墙
    3. 弓箭手、野蛮人在主攻点撒开，躲在巨人后面输出
    4. 再等几秒，哥布林分到各个资源方向去抢钱
    5. 国王最后放在主攻点
    认不出的兵种按老办法一个卡槽一个卡槽下。"""
    img = screenshot()
    angles = _pick_angles(img)
    slots = identify_slots(img)
    log("下兵方向：" + "、".join(f"{a:.0f}°" for a in angles)
        + "；兵种：" + ("、".join(BAR_NAMES.get(k, k) for k in slots) or "没认出来"))
    if not slots:
        return _deploy_simple(find_all, tpl, log, angles)

    usable = [k for k, s in slots.items() if not _card_empty(img, s)]
    first = next((k for k in ("giant", "barb", "archer", "goblin", "breaker", "king") if k in usable), None)
    if not first:
        return

    # 开口：主攻方向左右两侧各放几个野蛮人，先清掉外围建筑，巨人才会往中心走，不会绕着外圈打
    if "barb" in usable and "giant" in usable:
        tap(SLOT_X0 + SLOT_DX * slots["barb"], SLOT_Y)
        time.sleep(0.4)
        sides = [p for p in (_find_edge(find_all, tpl, slots["barb"], angles[0] + d) for d in (-40, 40)) if p]
        if sides:
            _drop_all(find_all, tpl, slots["barb"], sides, 20, max_taps=FUNNEL_BARBS * len(sides))
            log(f"开口：两侧各放了 {FUNNEL_BARBS} 个野蛮人")
            time.sleep(4)

    tap(SLOT_X0 + SLOT_DX * slots[first], SLOT_Y)
    time.sleep(0.4)
    p = _find_edge(find_all, tpl, slots[first], angles[0])
    if p:  # 主方向找到了，其他资源方向找到几个算几个（给哥布林用）
        points = [p] + [q for q in (_find_edge(find_all, tpl, slots[first], a) for a in angles[1:]) if q]
    else:
        points = _edges(find_all, tpl, slots[first], angles)
    if not points:
        log("没找到能放兵的位置")
        return
    main = [points[0]]
    plan = [  # (兵种, 放在哪, 撒开半径, 放之前等几秒)
        ("giant", main, 15, 0),
        ("breaker", main, 10, 3),
        ("archer", main, 50, 1),
        ("barb", main, 50, 0),
        ("goblin", points, 40, 4),
        ("king", main, 5, 1),
    ]
    for kind, pts, spread, wait in plan:
        if kind not in usable:
            continue
        img = screenshot()
        if not _in_battle(find_all, tpl, img):
            return
        if _card_empty(img, slots[kind]):
            continue
        time.sleep(wait)
        if kind == "king":  # 英雄放下后卡片不会变灰，只点一下
            tap(SLOT_X0 + SLOT_DX * slots[kind], SLOT_Y)
            time.sleep(0.3)
            tap(*pts[0])
            continue
        _drop_all(find_all, tpl, slots[kind], pts, spread, per_tap=0.25 if kind in ("giant", "breaker") else 0.1)
    # 没认出来的卡槽（新解锁的兵种）也放掉
    known = set(slots.values())
    for slot in range(SLOTS):
        img = screenshot()
        if slot in known or _card_empty(img, slot) or not _in_battle(find_all, tpl, img):
            continue
        _drop_all(find_all, tpl, slot, main, 40)


def _deploy_simple(find_all, tpl, log, angles):
    """老办法：一个卡槽一个卡槽，在各个方向分批下。"""
    for slot in range(SLOTS):
        img = screenshot()
        if not _in_battle(find_all, tpl, img):
            return
        if _card_empty(img, slot):
            continue
        tap(SLOT_X0 + SLOT_DX * slot, SLOT_Y)
        time.sleep(0.4)
        points = []
        for a in angles:
            p = _find_edge(find_all, tpl, slot, a)
            if p:
                points.append(p)
        if not points:  # 朝资源的方向都不行，转一圈找
            for a in range(0, 360, 30):
                p = _find_edge(find_all, tpl, slot, a)
                if p:
                    points.append(p)
                    break
        if not points:
            log("没找到能放兵的位置")
            continue
        # 分批放：每个点先放一批，隔几秒再放下一批，让前面的兵先吸引火力
        for round_ in range(4):
            for x, y in points:
                _drop(x, y, 6)
            if _card_empty(screenshot(), slot):
                break
            time.sleep(random.uniform(1.5, 3))
        for _ in range(10):  # 剩下的全放到第一个点（最多再放 100 下，防止死循环）
            img = screenshot()
            if _card_empty(img, slot) or not _in_battle(find_all, tpl, img):
                break
            _drop(*points[0], 10)


# ---------- 整场流程 ----------

def attack(find_all, tpl, log, need_gold=True, need_elixir=True, plan=""):
    """打一场，成功回到村庄返回 True。need_* 表示这种资源还没满、值得抢；plan 是这场用的配兵（记进战果）。"""
    if not (need_gold or need_elixir):
        log("金币和圣水都满了，不进攻")
        return False
    img = screenshot()
    pts = find_all(img, tpl["home_attack"], 0.8)
    if not pts:
        log("找不到村庄左下角的进攻按钮，这场不打（截图存在 debug\进攻前.png）")
        save_debug(img, "进攻前")
        return False
    tap(*pts[0])
    btn = _wait_for(find_all, tpl["btn_search"], 8)
    if not btn:
        log("没找到搜索对手按钮（截图存在 debug\没找到搜索按钮.png）")
        save_debug(screenshot(), "没找到搜索按钮")
        return False
    tap(*btn)
    btn = _wait_for(find_all, tpl["btn_army_attack"], 8)
    if btn:  # 有时会先弹出军队界面
        tap(*btn)
    nxt = _wait_for(find_all, tpl["btn_next"], 30)
    if not nxt:
        log("没进入战斗画面")
        return False

    # 看对手：战利品太少就换下一个
    for skip in range(MAX_SKIPS + 1):
        time.sleep(2)  # 等村庄加载完、数字显示出来
        ok, gold, elixir = worth_attacking(screenshot(), need_gold, need_elixir, MIN_GOLD, MIN_ELIXIR)
        if ok or skip == MAX_SKIPS:
            log(f"对手战利品 金币 {gold} / 圣水 {elixir}，" + ("开打" if ok else f"已经换了 {MAX_SKIPS} 个，将就打"))
            skips, offered, started = skip, (gold, elixir), time.time()
            break
        log(f"对手战利品 金币 {gold} / 圣水 {elixir}，太少，换下一个")
        tap(*nxt)
        nxt = _wait_for(find_all, tpl["btn_next"], 30)
        if not nxt:
            log("换对手后没回到战斗画面")
            return False

    time.sleep(random.uniform(1, 2))
    _deploy(find_all, tpl, log)

    # 结算页的按钮可能是"回营"，也可能是"领取奖励"（三星时）；战利品抢光了就提前结束，不干等倒计时
    btn, left = _wait_battle_end(find_all, tpl, log)
    if btn:
        time.sleep(1.5)  # 等结算页的数字滚完
        got = read_result(screenshot())
        from .stats import record
        tg, te, ratio = record(plan, offered, left, time.time() - started, skips, got)
        log(f"本场抢到 金币 {tg} / 圣水 {te}（对手的 {ratio:.0%}），配兵：{plan or '未知'}")
    if not btn:
        log("战斗超时没结束")
        return False
    time.sleep(random.uniform(1, 2))
    claimed = bool(find_all(screenshot(), tpl["btn_claim"], 0.8))
    tap(*btn)
    log("战斗结束，" + ("领取奖励" if claimed else "回营"))
    time.sleep(4)
    if claimed:
        _open_chest(find_all, tpl, log)
    return True


def read_result(img):
    """结算页"您得到了"下面的金币、圣水数字，读不出的是 None。"""
    return read_number(img[418:466, 680:845]), read_number(img[486:534, 680:845])


def _green_button(img):
    """画面中间最大的绿色按钮（确认框的"确定"）的中心，没有返回 None。"""
    hsv = cv2.cvtColor(img[350:750, 300:1300], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 35) & (hsv[..., 0] <= 75) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).astype("uint8")
    n, _, st, c = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    k = 1 + int(st[1:, cv2.CC_STAT_AREA].argmax())
    return (int(c[k][0]) + 300, int(c[k][1]) + 350) if st[k, cv2.CC_STAT_AREA] > 8000 else None


def _wait_battle_end(find_all, tpl, log):
    """等战斗结算页，返回 (结算按钮, 最后读到的剩余战利品)。
    左上角剩余战利品连续两次都是 0（抢光了），就点"结束战斗"提前收工。"""
    end = time.time() + BATTLE_TIMEOUT
    zeros = 0
    left = [None, None]  # 最后一次读到的剩余战利品（结算页就读不到了）
    while time.time() < end:
        img = screenshot()
        for t in (tpl["btn_return"], tpl["btn_claim"]):
            pts = find_all(img, t, 0.8)
            if pts:
                return pts[0], tuple(left)
        gold, elixir = read_loot(img)
        if gold is not None and elixir is not None:
            left = [gold, elixir]
        zeros = zeros + 1 if gold == 0 and elixir == 0 else 0
        if zeros >= 2:
            btn = find_all(img, tpl["btn_end_battle"], 0.8)
            if btn:
                log("战利品已经抢光，提前结束战斗")
                tap(*btn[0])
                time.sleep(1.5)
                img = screenshot()
                if not (find_all(img, tpl["btn_return"], 0.8) or find_all(img, tpl["btn_claim"], 0.8)):
                    save_debug(img, "结束战斗_确认框")
                    ok = _green_button(img)
                    if ok:
                        tap(*ok)
                zeros = -100  # 只提前结束一次
        time.sleep(5)
    return None, tuple(left)


def _open_chest(find_all, tpl, log):
    """三星后的宝箱动画：点画面中间推进，看到"继续"就点，直到回到村庄。"""
    for _ in range(15):
        img = screenshot()
        if find_all(img, tpl["home_attack"], 0.8):
            return
        btn = find_all(img, tpl["btn_continue"], 0.8)
        tap(*(btn[0] if btn else (800, 450)))
        time.sleep(2)
    log("开宝箱后没回到村庄")


def _card_unlocked(img, x, y):
    """兵种卡片是彩色的（已解锁），灰色就是还没解锁。"""
    hsv = cv2.cvtColor(img[y - 60:y + 60, x - 60:x + 60], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) > 60


# 兵种编辑里的卡片：代号 → (名字, 位置, 人口)。第一排 y=615，第二排 y=785
ARMY_CARDS = {
    "barb": ("野蛮人", (118, 615), 1),
    "giant": ("巨人", (280, 615), 5),
    "breaker": ("炸弹人", (445, 615), 2),
    "archer": ("弓箭手", (118, 785), 1),
    "goblin": ("哥布林", (280, 785), 1),
}
TRASH = (1545, 210)           # 清空军队的垃圾桶
TRASH_OK = (970, 580)         # "移除部队？"的确定
_army_plan = None             # 现在军队是按哪套方案配的（None＝这次启动还没配过）


def _read_capacity(img):
    """读军队界面"75/90"斜杠后面的总人口，读不出返回 None。
    斜杠和数字一样是白字，靠"笔画占外框的比例"认出来：斜杠是细斜线，占比不到 0.42。"""
    from .scout import SIZE, _DIGITS
    hsv = cv2.cvtColor(img[192:228, 720:810], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype("uint8")
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    comps = sorted((st[k, 0], st[k, 1], st[k, 2], st[k, 3], st[k, 4]) for k in range(1, n)
                   if 13 <= st[k, 3] <= 22 and st[k, 4] >= 25)
    slash = [c for c in comps if c[4] / (c[2] * c[3]) < 0.42]
    if not slash:
        return None
    text = ""
    for x, y, w, h, _ in comps:
        if x > slash[0][0]:
            g = cv2.resize(m[y:y + h, x:x + w] * 255, SIZE, interpolation=cv2.INTER_AREA).astype(float)
            text += str(min(_DIGITS, key=lambda d: np.abs(_DIGITS[d] - g).mean()))
    return int(text) if text and 20 <= int(text) <= 400 else None


def fill_army(find_all, tpl, log, plan):
    """按配兵方案 plan（stats.PLANS 里的名字）配兵：方案换了、人口没满（数字旁有红色感叹号）、
    或者这次启动还没配过，就清空重配。没解锁的兵种跳过，剩下的人口补野蛮人。军队是免费的，重配不花钱。"""
    global _army_plan
    from .stats import PLANS
    tap(65, 652)
    time.sleep(2)
    img = screenshot()
    if _army_plan == plan and not find_all(img, tpl["army_not_full"], 0.85):
        tap(1545, 80)
        time.sleep(1)
        return False
    cap = _read_capacity(img) or 90
    tap(TRASH[0], TRASH[1])  # 清空（空军队时垃圾桶是灰的，点了没反应）
    time.sleep(1.2)
    tap(*TRASH_OK)
    time.sleep(1.2)
    tap(1000, 300)  # 打开兵种编辑
    time.sleep(1.5)
    img = screenshot()
    used, parts = 0, []
    for kind, share in PLANS[plan]:  # 按方案顺序加，战斗时兵种栏也是这个顺序
        name, card, size = ARMY_CARDS[kind]
        if not _card_unlocked(img, *card):
            continue
        n = int(cap * share) // size
        for _ in range(n):
            tap(*card)
            time.sleep(0.12)
        if n:
            used += n * size
            parts.append(f"{name}{n}")
    barb = ARMY_CARDS["barb"][1]
    for i in range(cap - used + 5):  # 剩下的补野蛮人（多点几下也没关系，满了点不进去）
        tap(*barb)
        time.sleep(0.1)
        if i % 10 == 9 and not find_all(screenshot(), tpl["army_not_full"], 0.85):
            break
    parts.append("野蛮人补满")
    log(f"军队按\"{plan}\"配好（{cap} 人口）：" + "、".join(parts))
    _army_plan = plan
    tap(1545, 80)
    time.sleep(1)
    tap(1545, 80)
    time.sleep(1)
    return True
