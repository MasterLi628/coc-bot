"""自动进攻：配兵 → 搜索对手 → 找资源建筑 → 按兵种分工下兵 → 等战斗结束 → 回营。

下兵思路：
1. 截图找对手的金矿/圣水收集器/储罐（金黄色色块），离地图中心越远的越可能在城墙外、没人保护；
   一个都认不出就随机挑一边（颜色判断现在经常认不出）
2. 朝着这些资源建筑的方向，从屏幕外沿射线一步步往里试点；每试一下看兵种卡上的数字变没变，
   第一次变了就是能放兵的地图边缘（一定在红线外）
3. 兵种栏按卡片边框找位置、按头像认兵种；开口 → 肉盾 → 输出 → 国王 → 法术 → 剩下的兵（援军、奖励的兵）
4. 活动期间战斗中和结算前的三选一奖励自动选
"""
import math
import random
import time

import cv2
import numpy as np

from .adbtool import screenshot, swipe, tap
from .config import CFG, TEMPLATES, imread, save_debug
from .scout import read_loot, read_number, worth_attacking

# 兵种卡槽（底部栏）：卡宽约 110，同一组卡间隔 121，兵种/援军/英雄/法术分组之间还多空十几像素，
# 所以每张卡的位置由 _slot_xs 按左边框现找；找不到时才按 SLOT_X0 + SLOT_DX * 卡槽号 估
SLOT_X0, SLOT_DX, SLOT_Y = 190, 121, 815
SLOTS = 10                   # 最多看几个卡槽（兵种 + 英雄 + 法术 + 援军 + 战斗中奖励的兵），空槽会跳过
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

_XS = []  # 最近一次 identify_slots 找到的各卡中心 x


def _sx(slot):
    """卡槽中心 x。"""
    return _XS[slot] if slot < len(_XS) else SLOT_X0 + SLOT_DX * slot


def _slot_blank(img, x):
    """这一格没有卡（虚线框，透出后面的地图：颜色鲜但暗）。用完的卡是灰的（饱和度低），不算空格。"""
    hsv = cv2.cvtColor(img[760:870, x - 50:x + 50], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) > 100 and float(hsv[..., 2].mean()) < 110


def _slot_xs(img):
    """兵种栏每张卡的中心 x：从第一张卡的左边框开始，在后面 116~143 像素里找下一张的左边框（竖直边缘最集中的列），
    一直找到空格为止。"""
    g = cv2.cvtColor(img[748:885], cv2.COLOR_BGR2GRAY).astype(float)
    prof = (np.abs(cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)) > 60).mean(axis=0)

    def edges(a, b):
        return [x for x in range(max(a, 2), min(b, 1598)) if prof[x] > 0.5 and prof[x] == prof[x - 2:x + 3].max()]

    left = (edges(100, 170) or [SLOT_X0 - 55])[0]
    xs = []
    while left + 110 < 1600 and len(xs) < SLOTS:
        if _slot_blank(img, left + 55):
            break
        xs.append(left + 55)
        left = (edges(left + 116, left + 143) or [left + SLOT_DX])[0]
    return xs


def _card(img, slot):
    x = _sx(slot)
    return cv2.cvtColor(img[CARD_NUM[0]:CARD_NUM[1], x - 55:x + 55], cv2.COLOR_BGR2GRAY)


def _card_changed(before, after):
    return float(np.abs(before.astype(int) - after.astype(int)).mean()) > 6


def _card_empty(img, slot):
    """卡片用完后会变灰（饱和度 6~16），或者整格空着（暗，亮度 30~90）。
    骷髅巨人这种白色调的卡饱和度也有 40 多，不能拿 45 当界线。"""
    x = _sx(slot)
    hsv = cv2.cvtColor(img[760:870, x - 50:x + 50], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) < 25 or float(hsv[..., 2].mean()) < 100


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
        if _card_empty(img, slot):  # 卡已经用光了，再点也放不出兵（以前会白点三十来下、浪费半分钟）
            return None
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

# 兵种代号 → (名字, 人口)。兵种编辑和战斗栏都按头像模板认，新兵种插在前面也不怕卡片挪位置
ARMY_CARDS = {
    "barb": ("野蛮人", 1), "archer": ("弓箭手", 1), "giant": ("巨人", 5), "goblin": ("哥布林", 1),
    "breaker": ("炸弹人", 2), "totem": ("图腾投手", 28), "witchgolem": ("女巫戈仑", 41),
    "ghost": ("皇家幽灵", 8), "lavaloon": ("熔岩气球", 21), "skelgiant": ("骷髅巨人", 20),
}
# templates/army：兵种编辑里的卡片头像
_ARMY = {p.stem: cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY) for p in sorted((TEMPLATES / "army").glob("*.png"))}
# templates/bar：战斗栏卡片头像；没有截过的兵种用编辑界面的头像缩小代替（大小正好差 0.73 倍）
_BAR = {p.stem: cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY) for p in sorted((TEMPLATES / "bar").glob("*.png"))}
_BAR.update({k: cv2.resize(t, None, fx=0.73, fy=0.73, interpolation=cv2.INTER_AREA)
             for k, t in _ARMY.items() if k not in _BAR})
BAR_NAMES = {**{k: v[0] for k, v in ARMY_CARDS.items()}, "king": "野蛮人之王", "lightning": "雷电法术"}
TANKS = ("witchgolem", "skelgiant", "giant")  # 肉盾，开口的野蛮人就是给它们清外围的
SPELLS = ("lightning",)                       # 法术卡：不能当兵撒，要瞄准了放
NOT_TROOPS = ("king",) + SPELLS               # 补放剩下的兵时跳过这些
# 战斗中途（活动期间）弹出的"选择一项奖励！"三选一，按 800x450 缩小图截的模板
_REWARD = cv2.cvtColor(imread(TEMPLATES / "reward_title_half.png"), cv2.COLOR_BGR2GRAY)
REWARD_PICK = (480, 470)                      # 三张卡最左边那张（目前都是成群的兵，比金币/活动货币有用）
# 赢了之后（活动期间）结算前还有一次三选一：金币 / 圣水 / 活动货币，横幅颜色暗一些，单独一个模板
_END_REWARD = cv2.cvtColor(imread(TEMPLATES / "end_reward_half.png"), cv2.COLOR_BGR2GRAY)
END_CARDS = (234, 400, 568)                   # 缩小图里三张卡的中心 x（图标在 y 205~245）


def identify_slots(img):
    """兵种栏每个卡槽是什么兵，返回 {兵种: 卡槽号}。用灰度比对，卡片用完变灰也认得出。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _XS[:] = _slot_xs(img)
    found = {}
    for slot, x in enumerate(_XS):
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


def _take_reward(img, log):
    """战斗中弹出"选择一项奖励！"就选最左边那张，返回 True。选到的兵会插到兵种栏前面，后面的卡槽号跟着后移。
    不选的话倒计时完会自动选（常常是活动货币），撒兵的点击还可能误点到某张卡。"""
    small = cv2.cvtColor(cv2.resize(img, (800, 450), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    if float(cv2.matchTemplate(small[40:120, 250:550], _REWARD, cv2.TM_CCOEFF_NORMED).max()) < 0.8:
        return False
    tap(*REWARD_PICK)
    time.sleep(1.5)
    if log:
        log("战斗中弹出三选一奖励，选了最左边那张")
    return True


def _take_end_reward(img, log, need_gold, need_elixir):
    """结算前的三选一：没满的资源优先拿金币、再圣水（卡上有红色"已满！"的不拿），都满了拿活动货币。选了返回 True。"""
    small = cv2.resize(img, (800, 450), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small[80:180, 250:550], cv2.COLOR_BGR2GRAY)
    if float(cv2.matchTemplate(gray, _END_REWARD, cv2.TM_CCOEFF_NORMED).max()) < 0.85:
        return False
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    cards = {}
    for cx in END_CARDS:
        icon = hsv[205:245, cx - 25:cx + 25]
        vivid = icon[..., 1] > 120
        hue = float(icon[..., 0][vivid].mean()) if vivid.any() else -1
        tag = hsv[155:172, cx - 70:cx - 20]
        full = float(((tag[..., 1] > 120) & ((tag[..., 0] < 8) | (tag[..., 0] > 170))).mean()) > 0.3
        kind = "金币" if 15 <= hue <= 35 else "圣水" if 110 <= hue <= 165 else "活动货币"
        cards.setdefault(kind, (cx, full))
    pick = next((k for k, need in (("金币", need_gold), ("圣水", need_elixir))
                 if need and k in cards and not cards[k][1]), None)
    pick = pick or ("活动货币" if "活动货币" in cards else next(iter(cards)))
    tap(cards[pick][0] * 2, 500)
    time.sleep(1.5)
    log(f"战斗结束的三选一奖励：拿了{pick}")
    return True


def _drop_all(find_all, tpl, slot, points, spread, per_tap=0.1, max_taps=150, check=8, log=None, hold=False):
    """选中卡槽，在几个点之间轮流撒兵直到这张卡用完。spread 是撒开的半径（像素）；
    每放 check 下截图看一次卡用完没有（大块头一共就一两个，要每下都看）。
    hold：按住沿边拖一段，兵会连续放出来，几十个的小兵比一下下点快得多（ADB 点一下要 0.3 秒）。
    中途弹出三选一奖励时选完就返回 True（卡槽号变了，这张卡剩下的兵交给 _deploy_rest）。"""
    tap(_sx(slot), SLOT_Y)
    time.sleep(0.3)
    for i in range(max_taps // check if hold else max_taps):
        x, y = points[i % len(points)]
        if hold:
            swipe(x - spread, y - spread // 3, x + spread, y + spread // 3, 1500)
            img = screenshot()
            if _take_reward(img, log):
                return True
            if _card_empty(img, slot) or not _in_battle(find_all, tpl, img):
                return False
            continue
        px, py = x + random.randint(-spread, spread), y + random.randint(-spread // 2, spread // 2)
        if not _blocked(px, py) and SCREEN_Y[0] <= py <= SCREEN_Y[1]:
            tap(px, py)
            time.sleep(per_tap)
        if i % check == check - 1:
            img = screenshot()
            if _take_reward(img, log):
                return True
            if _card_empty(img, slot) or not _in_battle(find_all, tpl, img):
                return False
    return False


def _deploy_rest(find_all, tpl, log, main):
    """兵种栏里还有兵的卡（没认出来的、奖励来的南瓜野蛮人之类、部落援军、之前没放完的）全部放到主攻点。"""
    tried = set()  # 放过一遍还没变灰的卡（比如认不出的英雄）不再反复点
    for _ in range(SLOTS):  # 每放完一张重新认一遍，中途又弹奖励卡槽会挪
        img = screenshot()
        if not _in_battle(find_all, tpl, img):
            return
        if _take_reward(img, log):
            img, tried = screenshot(), set()
        skip = tried | {s for k, s in identify_slots(img).items() if k in NOT_TROOPS}
        rest = [s for s in range(len(_XS)) if s not in skip and not _card_empty(img, s)]
        if not rest:
            return
        if _drop_all(find_all, tpl, rest[0], main, 40, log=log, hold=True):
            tried = set()
        else:
            tried.add(rest[0])


def _cast_spells(find_all, tpl, log, scene, main):
    """雷电法术打主攻点附近的资源建筑（兵正要去打的那个），帮它们快点拆掉、拿到里面的钱。
    scene 是下兵前的截图（兵还没挡住建筑，好认）。"""
    img = screenshot()
    slots = identify_slots(img)
    targets = [(x, y) for x, y, _ in loot_targets(scene)]
    for kind in SPELLS:
        if kind not in slots or _card_empty(img, slots[kind]):
            continue
        if not targets:  # 盲打试过：下兵点在地图边上，往中心走一段还是草地，打空了。认不出目标就留着（法术免费，不亏）
            log(f"没认出资源建筑，{BAR_NAMES[kind]}留着不放")
            continue
        x, y = min(targets, key=lambda t: math.hypot(t[0] - main[0], t[1] - main[1]))
        tap(_sx(slots[kind]), SLOT_Y)
        time.sleep(0.3)
        for _ in range(6):  # 一次放一个，放到卡变灰为止
            tap(x, y)
            time.sleep(0.5)
            if _card_empty(screenshot(), slots[kind]):
                break
        log(f"{BAR_NAMES[kind]}放在 ({x}, {y})")


def _edges(find_all, tpl, slot, angles):
    """用这张卡沿各个方向找能放兵的边缘（每找到一个点会放出 1 个兵）。"""
    tap(_sx(slot), SLOT_Y)
    time.sleep(0.4)
    points = [p for p in (_find_edge(find_all, tpl, slot, a) for a in angles) if p]
    if not points:  # 朝资源的方向都不行，转一圈找
        for a in range(0, 360, 30):
            p = _find_edge(find_all, tpl, slot, a)
            if p:
                return [p]
    return points


# 下兵打法（单人模式练兵时轮流试，见 practice.py）：
# funnel 开口；routes 主攻点分几路（1＝全压一点，2＝两个方向各一半）；
# follow 肉盾放下后再等几秒才放输出兵（让肉盾先吃防御火力）
STYLES = {
    "标准": {"funnel": True, "routes": 1, "follow": 0},
    "不开口": {"funnel": False, "routes": 1, "follow": 0},
    "两路": {"funnel": True, "routes": 2, "follow": 0},
    "肉盾先走": {"funnel": True, "routes": 1, "follow": 5},
}
STYLE = "标准"  # 联机进攻用的打法


def _deploy(find_all, tpl, log, style=None):
    """按兵种分工下兵，返回主攻点（下兵失败返回 None）：
    1. 开口：有野蛮人和肉盾时，主攻方向两侧各放几个野蛮人清外围
    2. 用还有兵的小兵试出能放兵的边缘（用光了换下一张卡）
    3. 肉盾（女巫戈仑、骷髅巨人、巨人）放主攻点，炸弹人跟上炸墙
    4. 熔岩气球、图腾投手、弓箭手、野蛮人在主攻点输出；哥布林、皇家幽灵分到各个资源方向
    5. 国王最后放在主攻点；雷电打离主攻点最近的资源建筑
    6. 还有兵的卡（部落援军、奖励的兵、认不出的兵）全部放到主攻点
    兵种栏一张都认不出时，按老办法一个卡槽一个卡槽下。"""
    img = scene = screenshot()
    angles = _pick_angles(img)
    slots = identify_slots(img)
    log("下兵方向：" + "、".join(f"{a:.0f}°" for a in angles)
        + "；兵种：" + ("、".join(BAR_NAMES.get(k, k) for k in slots) or "没认出来"))
    if not slots:
        return _deploy_simple(find_all, tpl, log, angles)

    usable = [k for k, s in slots.items() if not _card_empty(img, s)]
    if not usable:
        return
    st = STYLES[style or STYLE]

    # 开口：主攻方向左右两侧各放几个野蛮人，先清掉外围建筑，肉盾才会往中心走，不会绕着外圈打
    if st["funnel"] and "barb" in usable and any(k in usable for k in TANKS):
        tap(_sx(slots["barb"]), SLOT_Y)
        time.sleep(0.4)
        sides = [p for p in (_find_edge(find_all, tpl, slots["barb"], angles[0] + d) for d in (-40, 40)) if p]
        if sides:
            _drop_all(find_all, tpl, slots["barb"], sides, 20, max_taps=FUNNEL_BARBS * len(sides))
            log(f"开口：两侧各放了 {FUNNEL_BARBS} 个野蛮人")
            time.sleep(4)

    # 试探边缘用便宜的小兵（每试出一个点就放下 1 个，用大块头试会把它们浪费在别的方向）。
    # 开口可能已经把野蛮人用光了，所以重新看一眼哪些卡还有兵
    points = []
    for first in ("barb", "archer", "goblin", "ghost", "giant", "breaker", "skelgiant", "totem",
                  "lavaloon", "witchgolem", "king"):
        if first not in usable or _card_empty(screenshot(), slots[first]):
            continue
        tap(_sx(slots[first]), SLOT_Y)
        time.sleep(0.4)
        p = _find_edge(find_all, tpl, slots[first], angles[0])
        if p:  # 主方向找到了，其他资源方向找到几个算几个（给哥布林用；试探的卡用光了就不试了）
            points = [p] + [q for q in (_find_edge(find_all, tpl, slots[first], a) for a in angles[1:]
                                        if not _card_empty(screenshot(), slots[first])) if q]
        else:
            points = _edges(find_all, tpl, slots[first], angles)
        if points or not _card_empty(screenshot(), slots[first]):
            break  # 找到了；或者卡还有兵却哪都放不下，换卡也一样
    if not points:
        log("没找到能放兵的位置")
        return
    main = points[:st["routes"]]
    follow = st["follow"]
    plan = [  # (兵种, 放在哪, 撒开半径, 放之前等几秒)
        ("witchgolem", main, 10, 0),  # 女巫戈仑：血厚，还会召唤骷髅
        ("skelgiant", main, 15, 0),   # 骷髅巨人：专打防御，死了还会爆炸
        ("giant", main, 15, 0),
        ("breaker", main, 10, 3),
        ("lavaloon", main, 15, 1),    # 熔岩气球：先去打防空
        ("totem", main, 20, 2 + follow),  # 图腾投手：射程远，躲在肉盾后面输出
        ("archer", main, 50, 1 + follow),
        ("barb", main, 50, 0),
        ("goblin", points, 40, 4),
        ("ghost", points, 30, 0),     # 皇家幽灵：分到各个资源方向
        ("king", main, 5, 1),
    ]
    for kind, pts, spread, wait in plan:
        if kind not in usable:
            continue
        img = screenshot()
        if not _in_battle(find_all, tpl, img):
            return main[0]
        if _take_reward(img, log):
            img = screenshot()
        slots = identify_slots(img)  # 中途选了奖励，卡槽会整体后移，每次重新认
        if kind not in slots or _card_empty(img, slots[kind]):
            continue
        time.sleep(wait)
        if kind == "king":  # 英雄放下后卡片不会变灰，只点一下
            tap(_sx(slots[kind]), SLOT_Y)
            time.sleep(0.3)
            tap(*pts[0])
            continue
        big = ARMY_CARDS[kind][1] >= 20
        _drop_all(find_all, tpl, slots[kind], pts, spread, per_tap=0.25 if big or kind in ("giant", "breaker") else 0.1,
                  check=1 if big else 8, log=log, hold=ARMY_CARDS[kind][1] == 1)
    if _in_battle(find_all, tpl, screenshot()):
        _cast_spells(find_all, tpl, log, scene, main[0])
    # 没认出来的卡（新兵种、部落援军、奖励的兵）和前面没放完的也放掉
    _deploy_rest(find_all, tpl, log, main)
    return main[0]


def _deploy_simple(find_all, tpl, log, angles):
    """老办法：一个卡槽一个卡槽，在各个方向分批下。"""
    for slot in range(len(_XS) or SLOTS):
        img = screenshot()
        if not _in_battle(find_all, tpl, img):
            return
        if _card_empty(img, slot):
            continue
        tap(_sx(slot), SLOT_Y)
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
    tip = not btn and find_all(screenshot(), tpl["tip_bubble"], 0.8)
    if tip:  # 村姑的提示对话框（比如新开了排位战）盖住了搜索按钮，点一下对话框让她说完
        log("进攻页面有提示对话框，点掉")
        for _ in range(5):
            tap(tip[0][0] + 150, tip[0][1] + 60)
            time.sleep(1.5)
            btn = _wait_for(find_all, tpl["btn_search"], 2)
            if btn:
                break
    if not btn:
        log("没找到搜索对手按钮（截图存在 debug\没找到搜索按钮.png）")
        save_debug(screenshot(), "没找到搜索按钮")
        tap(1300, 600)  # 点右边村庄空地收起进攻页面
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
    main = _deploy(find_all, tpl, log)

    # 结算页的按钮可能是"回营"，也可能是"领取奖励"（三星时）；战利品抢光了就提前结束，不干等倒计时
    btn, left, snap = _wait_battle_end(find_all, tpl, log, main, (need_gold, need_elixir))
    if btn:
        time.sleep(1.5)  # 等结算页的数字滚完
        got = read_result(screenshot())
        from .stats import record
        tg, te, ratio = record(plan, offered, left, time.time() - started, skips, got)
        log(f"本场抢到 金币 {tg} / 圣水 {te}（对手的 {ratio:.0%}），配兵：{plan or '未知'}")
        if ratio < 0.05 and snap is not None:  # 留一张战斗中途的截图，方便查为什么没打下来
            save_debug(snap, "没抢到的战斗")
            log("这场几乎没抢到，战斗中途的截图存在 debug\没抢到的战斗.png")
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


def _wait_battle_end(find_all, tpl, log, main=None, need=(True, True)):
    """等战斗结算页，返回 (结算按钮, 最后读到的剩余战利品, 下兵完 30 秒左右的截图)。
    左上角剩余战利品连续两次都是 0（抢光了），就点"结束战斗"提前收工。
    中途弹出三选一奖励就选一张，奖励的兵放到主攻点 main。"""
    start = time.time()
    end = start + BATTLE_TIMEOUT
    zeros = 0
    left = [None, None]  # 最后一次读到的剩余战利品（结算页就读不到了）
    snap = None
    while time.time() < end:
        img = screenshot()
        for t in (tpl["btn_return"], tpl["btn_claim"]):
            pts = find_all(img, t, 0.8)
            if pts:
                return pts[0], tuple(left), snap
        if snap is None and time.time() - start > 30:
            snap = img
        if _take_end_reward(img, log, *need):
            continue
        if _take_reward(img, log):
            if main:
                _deploy_rest(find_all, tpl, log, [main])
            continue
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
        time.sleep(3)  # 三选一奖励大约 8 秒倒计时，看得勤一点
    return None, tuple(left), snap


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
    """兵种卡片是彩色的（已解锁），灰色就是还没解锁。灰卡饱和度只有 6 左右，骷髅巨人这种白色调的彩卡也有 48。"""
    hsv = cv2.cvtColor(img[y - 60:y + 60, x - 60:x + 60], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) > 25


CARD_AREA = (520, 880)        # 兵种编辑里卡片区的 y 范围
# 兵种编辑的格子：卡片内容会挪，但格子位置是固定的（一屏看得到 9 列 x 2 行）
GRID_X = [118 + round(162.9 * i) for i in range(9)]
GRID_Y = (615, 785)
AVAILABLE = None              # 上次在兵种编辑里看到的已解锁兵种（None＝这次启动还没看过），挑配兵方案用
_unknown_seen = 0             # 这次启动已经提醒过几张认不出的卡片，数量没变就不重复提醒


def _find_cards(img):
    """兵种编辑里每种兵的卡片位置 {代号: (x, y)}，认不出的不在里面。用灰度比对，卡片变灰也认得出。"""
    gray = cv2.cvtColor(img[CARD_AREA[0]:CARD_AREA[1]], cv2.COLOR_BGR2GRAY)
    out = {}
    for k, t in _ARMY.items():
        _, v, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
        if v >= 0.8:
            out[k] = (x + t.shape[1] // 2, y + CARD_AREA[0] + t.shape[0] // 2)
    return out


def _unknown_cards(img, cards):
    """彩色（已解锁）但认不出是什么兵的卡片格子：新出的兵种，或者老兵种升级换了样子。"""
    return [(x, y) for y in GRID_Y for x in GRID_X
            if _card_unlocked(img, x, y) and all(abs(x - cx) > 50 or abs(y - cy) > 50 for cx, cy in cards.values())]


TRASH = (1545, 210)           # 清空军队的垃圾桶
TRASH_OK = (970, 580)         # "移除部队？"的确定
_army_plan = None             # 现在军队是按哪套方案配的（None＝这次启动还没配过）


def _read_capacity(img):
    """读军队界面"75/90"斜杠后面的总人口，读不出返回 None。
    斜杠和数字一样是白字，靠"笔画占外框的比例"认出来：斜杠是细斜线，占比不到 0.42。"""
    from .scout import SIZE, _DIGITS
    hsv = cv2.cvtColor(img[192:228, 720:860], cv2.COLOR_BGR2HSV)  # 3 位数人口（145/145）要宽一点
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


SPELL_AREA = (960, 527)       # 军队界面的法术栏，点一下打开法术编辑
EDITOR_CLOSE = (1400, 300)    # 法术编辑开着时点这里（攻城机器那一栏）收起编辑
_SPELL = {p.stem: cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY) for p in sorted((TEMPLATES / "spell").glob("*.png"))}
# 部落援军：没加部落，但"部落城堡蛋糕"活动期间可以免费选 15 人口的援军（平时要花突袭奖章，按钮不是"免费"就不碰）
CC_FREE = cv2.cvtColor(imread(TEMPLATES / "cc_free.png"), cv2.COLOR_BGR2GRAY)
_CC = {p.stem: cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY) for p in sorted((TEMPLATES / "cc").glob("*.png"))}
CC_TROOPS = (("wizard", 3), ("archer", 3))  # 法师 4 人口 x3 + 弓箭手 x3 = 15
CC_FREE_BTN, CC_PICK_AREA, CC_PICK_OK, CC_OK = (1333, 723), (800, 300), (800, 775), (990, 627)


def _not_full(find_all, tpl, img, part):
    """军队界面某一栏旁边有红色感叹号（没配满）。part：troops 兵种 / spells 法术 / cc 部落援军。
    三栏的感叹号长得一样，只能按高度分。"""
    y1, y2 = {"troops": (0, 260), "spells": (300, 470), "cc": (600, 720)}[part]
    return any(y1 <= y <= y2 for _, y in find_all(img, tpl["army_not_full"], 0.85))


def _match(img, t, threshold=0.8):
    """灰度模板在 img 里最像的位置中心，不够像返回 None。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, v, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED))
    return (x + t.shape[1] // 2, y + t.shape[0] // 2) if v >= threshold else None


def _fill_spells(find_all, tpl, log):
    """法术栏没满就打开法术编辑，按 templates/spell 里的顺序点已解锁的法术（法术和军队一样免费、立刻就好）。"""
    tap(*SPELL_AREA)
    time.sleep(1.5)
    img = screenshot()
    names = []
    for k, t in _SPELL.items():
        p = _match(img[CARD_AREA[0]:CARD_AREA[1]], t)
        if not p or not _card_unlocked(img, p[0], p[1] + CARD_AREA[0]):
            continue
        for _ in range(6):  # 多点几下也没关系，满了点不进去
            tap(p[0], p[1] + CARD_AREA[0])
            time.sleep(0.15)
        names.append(BAR_NAMES.get(k, k))
    tap(*EDITOR_CLOSE)
    time.sleep(1)
    if names:
        log("法术配好：" + "、".join(names))
    else:
        log("法术编辑里没认出能用的法术（截图存在 debug\法术编辑.png）")
        save_debug(img, "法术编辑")


def _fill_cc(find_all, tpl, log):
    """部落援军是"免费"的时候选满 15 人口。"""
    tap(*CC_FREE_BTN)
    time.sleep(1.5)
    tap(*CC_PICK_AREA)
    time.sleep(1.5)
    img = screenshot()
    picked = []
    for k, n in CC_TROOPS:
        p = _match(img, _CC[k])
        if not p:
            continue
        for _ in range(n):
            tap(*p)
            time.sleep(0.2)
        picked.append(f"{k}{n}")
    tap(*CC_PICK_OK)
    time.sleep(1.5)
    tap(*CC_OK)
    time.sleep(2)
    img = screenshot()
    if _not_full(find_all, tpl, img, "cc"):
        log("免费部落援军没选上（截图存在 debug\部落援军.png）")
        save_debug(img, "部落援军")
    else:
        log("免费部落援军已选好：法师、弓箭手")


def fill_army(find_all, tpl, log, plan):
    """按配兵方案 plan（stats.PLANS 里的名字）配兵：方案换了、人口没满（数字旁有红色感叹号）、
    或者这次启动还没配过，就清空重配。方案里有兵种现在没有（比如限时兵种下架了），就换一套配得出来的方案；
    剩下的人口补野蛮人。军队是免费的，重配不花钱。然后补满法术、（免费时）选部落援军。返回实际用的方案名。
    军队界面没打开（比如重启后活动广告晚一步弹出来盖住了村庄）返回 None，这轮不打，免得在活动页面里乱点。"""
    tap(65, 652)
    time.sleep(2)
    img = screenshot()
    if not find_all(img, tpl["army_title"], 0.8):
        log("军队界面没打开，这轮不打（截图存在 debug\\军队界面没打开.png）")
        save_debug(img, "军队界面没打开")
        return None
    if _army_plan != plan or _not_full(find_all, tpl, img, "troops"):
        plan = _fill_troops(find_all, tpl, log, plan, img)  # 配完会关掉军队界面
        tap(65, 652)
        time.sleep(2)
        img = screenshot()
    if _SPELL and _not_full(find_all, tpl, img, "spells"):
        _fill_spells(find_all, tpl, log)
        img = screenshot()
    if _not_full(find_all, tpl, img, "cc") and _match(img[680:770, 1260:1420], CC_FREE, 0.85):
        _fill_cc(find_all, tpl, log)
    tap(1545, 80)
    time.sleep(1)
    return plan


def _fill_troops(find_all, tpl, log, plan, img):
    """清空军队，按方案重配兵种，最后关掉军队界面。"""
    global _army_plan, AVAILABLE, _unknown_seen
    from .stats import PLANS, choose_plan
    cap = _read_capacity(img) or 90
    tap(TRASH[0], TRASH[1])  # 清空（空军队时垃圾桶是灰的，点了没反应）
    time.sleep(1.2)
    tap(*TRASH_OK)
    time.sleep(1.2)
    tap(1000, 300)  # 打开兵种编辑
    time.sleep(1.5)
    img = screenshot()
    cards = _find_cards(img)
    if not cards:  # 兵种编辑没打开（比如军队本来就是空的，清空那两下点偏了），再点一次
        tap(1000, 300)
        time.sleep(1.5)
        img = screenshot()
        cards = _find_cards(img)
    AVAILABLE = {k for k, p in cards.items() if _card_unlocked(img, *p)}
    unknown = _unknown_cards(img, cards)
    if len(unknown) > _unknown_seen:
        log(f"发现新兵种：兵种编辑里有 {len(unknown)} 张卡片认不出来（新出的兵种或者换了样子），暂时不用它们。"
            "截图存在 debug\新兵种.png，请找 Claude 补上")
        save_debug(img, "新兵种")
    _unknown_seen = max(_unknown_seen, len(unknown))
    missing = [ARMY_CARDS[k][0] for k, _ in PLANS[plan] if k not in AVAILABLE]
    if missing:
        new, _ = choose_plan(AVAILABLE)
        if new != plan:
            log(f"\"{plan}\"要用的{'、'.join(missing)}现在没有，改用\"{new}\"")
            plan = new
        else:
            log(f"\"{plan}\"要用的{'、'.join(missing)}现在没有，也没有配得齐的方案，缺的用野蛮人补")
    used, parts = 0, []
    for kind, share in PLANS[plan]:  # 按方案顺序加，战斗时兵种栏也是这个顺序
        name, size = ARMY_CARDS[kind]
        card = cards.get(kind)
        if not card or not _card_unlocked(img, *card):
            continue
        n = min(round(cap * share / size), (cap - used) // size)  # 大块头四舍五入，但不能超出剩下的人口
        for _ in range(n):
            tap(*card)
            time.sleep(0.12)
        if n:
            used += n * size
            parts.append(f"{name}{n}")
    barb = cards.get("barb")
    if not barb:
        log("兵种编辑里没认出野蛮人，剩下的人口没补（截图存在 debug\兵种编辑.png）")
        save_debug(img, "兵种编辑")
    for i in range(cap - used + 5 if barb else 0):  # 剩下的补野蛮人（多点几下也没关系，满了点不进去）
        tap(*barb)
        time.sleep(0.1)
        if i % 10 == 9 and not _not_full(find_all, tpl, screenshot(), "troops"):
            break
    if barb:
        parts.append("野蛮人补满")
    log(f"军队按\"{plan}\"配好（{cap} 人口）：" + "、".join(parts))
    _army_plan = plan
    tap(1545, 80)
    time.sleep(1)
    tap(1545, 80)
    time.sleep(1)
    return plan
