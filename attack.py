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

from adbtool import screenshot, tap
from config import CFG
from scout import worth_attacking

# 兵种卡槽（底部栏），从左数第 1 个起，间隔约 121 像素
SLOT_X0, SLOT_DX, SLOT_Y = 190, 121, 815
SLOTS = 4
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


def _deploy(find_all, tpl, log):
    angles = _pick_angles(screenshot())
    log("下兵方向：" + "、".join(f"{a:.0f}°" for a in angles))
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

def attack(find_all, tpl, log, need_gold=True, need_elixir=True):
    """打一场，成功回到村庄返回 True。need_* 表示这种资源还没满、值得抢。"""
    if not (need_gold or need_elixir):
        log("金币和圣水都满了，不进攻")
        return False
    pts = find_all(screenshot(), tpl["home_attack"], 0.8)
    if not pts:
        return False
    tap(*pts[0])
    btn = _wait_for(find_all, tpl["btn_search"], 8)
    if not btn:
        log("没找到搜索对手按钮")
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
            break
        log(f"对手战利品 金币 {gold} / 圣水 {elixir}，太少，换下一个")
        tap(*nxt)
        nxt = _wait_for(find_all, tpl["btn_next"], 30)
        if not nxt:
            log("换对手后没回到战斗画面")
            return False

    time.sleep(random.uniform(1, 2))
    _deploy(find_all, tpl, log)

    # 结算页的按钮可能是"回营"，也可能是"领取奖励"（三星时）
    btn = _wait_for(find_all, [tpl["btn_return"], tpl["btn_claim"]], BATTLE_TIMEOUT, interval=5)
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


ARCHER_CARD = (118, 785)      # 兵种编辑里第二排第一个（弓箭手）
BARB_CARD = (118, 615)        # 第一排第一个（野蛮人）


def _card_unlocked(img, x, y):
    """兵种卡片是彩色的（已解锁），灰色就是还没解锁。"""
    hsv = cv2.cvtColor(img[y - 60:y + 60, x - 60:x + 60], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) > 60


def fill_army(find_all, tpl, log):
    """军队人口没满（数字旁有红色感叹号）就补满：弓箭手解锁了就一半野蛮人一半弓箭手。"""
    tap(65, 652)
    time.sleep(2)
    if not find_all(screenshot(), tpl["army_not_full"], 0.85):
        tap(1545, 80)
        time.sleep(1)
        return False
    tap(1000, 300)  # 打开兵种编辑
    time.sleep(1.5)
    img = screenshot()
    cards = [BARB_CARD]
    if _card_unlocked(img, *ARCHER_CARD):
        cards.append(ARCHER_CARD)
    for i in range(80):
        tap(*cards[i % len(cards)])
        time.sleep(0.1)
        if i % 10 == 9 and not find_all(screenshot(), tpl["army_not_full"], 0.85):
            break
    log("军队人口已补满" + ("（野蛮人+弓箭手）" if len(cards) > 1 else "（野蛮人）"))
    tap(1545, 80)
    time.sleep(1)
    tap(1545, 80)
    time.sleep(1)
    return True
