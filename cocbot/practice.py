"""单人模式练兵：仓库满了、联机打了也白打的时候，去打单人模式的下一关，顺便比较几种下兵打法。

已经打过的关太简单（开口的野蛮人就能 100%），比不出好坏，所以只打"下一关"（地图上哥布林头像那一关）：
这关没打到 100% 就换一种打法再打，打到 100% 就自动进下一关。每场的打法和摧毁率记进 practice.csv，
攒够场数后按打法汇总，看哪种打法摧毁率高。
"""
import csv
import time
from datetime import datetime

from . import attack
from .adbtool import screenshot, tap
from .config import BASE, save_debug
from .scout import read_green, read_number

FILE = BASE / "practice.csv"
FIELDS = ["time", "style", "pct", "win", "try"]
SP_TAB = (500, 75)           # 进攻页面顶部的"单人模式"
PCT_BOX = (645, 705, 1495, 1590)  # 战斗中右下角"摧毁率"数字（y0, y1, x0, x1）
END_PCT_BOX = (125, 170, 740, 870)  # 结算页中间那颗星上的"摧毁率"（绿字）
MAX_TRIES = 8                # 同一关连续这么多场都没 100%，说明现在打不过，先歇着
REST = 6 * 3600

_tries = 0                   # 当前这关已经连续打了几场没 100%
_rest_until = 0


def load():
    try:
        with open(FILE, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def _record(style, pct, win):
    new = not FILE.exists()
    with open(FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "style": style,
                    "pct": pct, "win": int(win), "try": _tries})


def summary():
    """每种打法：(场数, 平均摧毁率, 胜率)。"""
    out = {}
    for r in load():
        n, p, w = out.get(r["style"], (0, 0.0, 0))
        out[r["style"]] = (n + 1, p + int(r["pct"]), w + int(r["win"]))
    return {k: (n, p / n, w / n) for k, (n, p, w) in out.items()}


def _pick_style():
    """这关第 1 场用目前平均摧毁率最高的打法（没打过的先试），之后每场换一种。"""
    names = list(attack.STYLES)
    stats = summary()
    untried = [k for k in names if k not in stats]
    best = untried[0] if untried else max(names, key=lambda k: stats[k][1])
    return names[(names.index(best) + _tries) % len(names)]


def _end_battle(find_all, tpl, img):
    """点"结束战斗"并确认，等结算页，返回"回营"按钮；退不出来返回 None。"""
    end = find_all(img, tpl["btn_end_battle"], 0.8)
    if end:
        tap(*end[0])
        time.sleep(1.5)
        ok = attack._green_button(screenshot())
        if ok:
            tap(*ok)
    return attack._wait_for(find_all, tpl["btn_return"], 15)


def resting():
    return time.time() < _rest_until


def practice(find_all, tpl, log):
    """打一场单人模式，回到村庄返回 True。"""
    global _tries, _rest_until
    pts = find_all(screenshot(), tpl["home_attack"], 0.8)
    if not pts:
        return False
    tap(*pts[0])
    if not attack._wait_for(find_all, tpl["btn_search"], 8):
        log("练兵：进攻页面没打开")
        tap(1300, 600)
        return False
    tap(*SP_TAB)
    btn = attack._wait_for(find_all, tpl["sp_attack"], 6)
    if not btn:
        log("练兵：单人模式地图上没找到\"进攻\"按钮（截图存在 debug\\练兵_地图.png）")
        save_debug(screenshot(), "练兵_地图")
        tap(1300, 600)
        return False
    style = _pick_style()
    log(f"练兵：打单人模式下一关（这关第 {_tries + 1} 场），打法\"{style}\"")
    tap(*btn)
    if not attack._wait_for(find_all, tpl["btn_end_battle"], 15):
        log("练兵：没进入战斗画面（截图存在 debug\\练兵_进战斗.png）")
        save_debug(screenshot(), "练兵_进战斗")
        return False
    time.sleep(1.5)
    save_debug(screenshot(), "练兵_开战")  # 每场覆盖一张，兵种栏没认全时拿来查
    attack._deploy(find_all, tpl, log, style)

    # 等结算页；单人模式没有可抢的资源，不能用联机那套"资源抢光就提前结束"
    pct, end, btn = 0, time.time() + attack.BATTLE_TIMEOUT, None
    while time.time() < end:
        img = screenshot()
        btn = find_all(img, tpl["btn_return"], 0.8) or find_all(img, tpl["btn_claim"], 0.8)
        if btn:
            break
        p = read_number(img[PCT_BOX[0]:PCT_BOX[1], PCT_BOX[2]:PCT_BOX[3]], drop_last=True)
        if p is not None and pct <= p <= 100:
            pct = p
        time.sleep(2)
    if not btn:
        img = screenshot()
        save_debug(img, "练兵_超时")
        log("练兵：战斗超时没结束，点\"结束战斗\"退出（截图存在 debug\\练兵_超时.png）")
        btn = _end_battle(find_all, tpl, img)
        if not btn:
            return False  # 退不出来就交给主循环（连续几轮不在村庄会重启游戏）
        _record(style, pct, False)
        _tries += 1
        tap(*btn)
        time.sleep(4)
        _back_home(find_all, tpl)
        return True
    time.sleep(1.5)
    img = screenshot()
    win = bool(find_all(img, tpl["sp_win"], 0.8))
    p = read_green(img[END_PCT_BOX[0]:END_PCT_BOX[1], END_PCT_BOX[2]:END_PCT_BOX[3]], drop_last=True)
    if p is not None and 0 <= p <= 100:
        pct = p  # 结算页的数字最准
    if win and pct < 50:
        pct = 50  # 摧毁率最后几下没读到；胜利至少 50%
    _record(style, pct, win)
    log(f"练兵结果：{'胜利' if win else '失败'}，摧毁率 {pct}%（打法\"{style}\"）")
    if pct >= 100:
        _tries = 0
        log("练兵：这关打满了，下次打下一关")
    else:
        _tries += 1
        if _tries >= MAX_TRIES:
            log(f"练兵：这关连续 {MAX_TRIES} 场没打满，先歇 6 小时（等兵种和建筑升级）")
            _tries, _rest_until = 0, time.time() + REST
    tap(*btn[0])
    time.sleep(4)
    if find_all(screenshot(), tpl["btn_continue"], 0.8):  # 过关可能有宝箱/奖励动画
        attack._open_chest(find_all, tpl, log)
    _back_home(find_all, tpl)
    return True


def _back_home(find_all, tpl):
    """打完回村庄。单人模式打输了点"回营"会回到关卡地图（进攻页面），要再点一下右边空地收起来。"""
    for _ in range(4):
        img = screenshot()
        if find_all(img, tpl["home_attack"], 0.8):
            return
        if find_all(img, tpl["sp_attack"], 0.8) or find_all(img, tpl["sp_tab"], 0.8) or find_all(img, tpl["btn_search"], 0.8):
            tap(1300, 600)
        else:
            save_debug(img, "练兵_回村")
        time.sleep(2)
