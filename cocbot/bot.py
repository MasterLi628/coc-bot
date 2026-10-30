"""部落冲突自动发育脚本：收资源 + 自动升级 + 自动进攻 + 掉线恢复。

用法：在控制面板点"启动挂机"，或者 python app.py --bot
按 Ctrl+C 停止。
日志同时写到 bot.log（打包版在 exe 旁边，源码版在项目根目录）。
"""
import os
import random
import sys
import subprocess
import time
import traceback

import cv2

from .adbtool import ADB, adb, device, screen_size, screenshot, swipe, tap
from . import config
from .config import CFG, PID_FILE, running_pid
from . import attack as attack_mod
from . import practice as practice_mod
from .attack import attack, fill_army
from .stats import choose_plan
from .lab import try_potion, try_research
from .pinch import pinch_out
from .scout import read_mask
from .upgrade import DESELECT, cancel_placement, close_windows, danger_on_screen, try_build_new, try_townhall, try_upgrade

TEMPLATES = config.TEMPLATES
LOG_FILE = config.BASE / "bot.log"
LOG_MAX = 2 * 1024 * 1024     # bot.log 超过这么大就改名成 bot.old.log（只留上一份），重新开一个
THRESHOLD = 0.85
PACKAGE = "com.tencent.tmgp.supercell.clashofclans"
# 下面几项在控制面板的"设置"页改（存在 config.json）
ATTACK = CFG["attack"]                          # 是否自动进攻
TOWNHALL_FIRST = CFG["townhall_first"]          # 大本营优先：钱够先升大本营，钱不够时只花圣水、攒金币
LOOP_MIN, LOOP_MAX = CFG["loop_min"], CFG["loop_max"]  # 每轮间隔秒数（随机）
MAX_NOT_HOME = CFG["max_not_home"]              # 连续几轮不在主界面就重启游戏
RESEARCH = CFG["research"]                      # 实验室自动研究
KEEP_BUILDERS = CFG["keep_builders"]            # 留几个工人给玩家自己用
LAB_INTERVAL = 1800                             # 每隔多少秒看一次实验室
ERROR_WAIT = 60               # 出错后等多少秒再重试


def load(name):
    return config.imread(TEMPLATES / f"{name}.png")


TPL = {name: load(name) for name in [
    "home_attack", "new_home_label", "collect_gold", "collect_elixir", "connection_lost", "upgrade_label",
    "game_update", "game_update_prompt", "upgrade_time",
    "builder_icon", "menu_suggest", "btn_upgrade", "confirm_label", "close_x",
    "btn_search", "btn_army_attack", "btn_army_attack2", "btn_edit_army", "btn_next", "btn_return", "result_label",
    "popup_busy", "gem_icon", "builders_zero", "townhall", "label_townhall",
    "shop_arrow", "place_ok", "place_cancel", "event_back", "popup_nogem", "btn_claim",
    "btn_continue", "army_not_full", "redline_msg",
    "web_back", "login_screen", "login_qq_auth", "label_lab", "btn_research", "lab_title", "th_locked", "btn_end_battle", "btn_reload", "btn_panel_collapse", "th_warn",
    "army_title", "tip_bubble", "sp_attack", "sp_win", "sp_tab", "end_confirm",
    "potion_btn", "potion_title", "potion_ok", "unit_hour", "unit_min",
    "unit_hour_s", "unit_min_s", "bpotion_btn", "bpotion_title", "bpotion_ok", "potion_none",
]}


def find_all(img, tpl, threshold=THRESHOLD):
    """返回所有匹配位置的中心点（已去重）。"""
    h, w = tpl.shape[:2]
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = (res >= threshold).nonzero()
    points = []
    for x, y in sorted(zip(xs, ys), key=lambda p: -res[p[1], p[0]]):
        cx, cy = x + w // 2, y + h // 2
        if all(abs(cx - px) > w or abs(cy - py) > h for px, py in points):
            points.append((cx, cy))
    return points


def find_all_scaled(img, tpl, threshold=THRESHOLD, scales=(0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.65)):
    """多尺寸匹配，应对画面缩放。"""
    points = []
    for s in scales:
        t = cv2.resize(tpl, None, fx=s, fy=s)
        for cx, cy in find_all(img, t, threshold):
            if all(abs(cx - px) > 20 or abs(cy - py) > 20 for px, py in points):
                points.append((cx, cy))
    return points


def jitter(v, r=4):
    return v + random.randint(-r, r)


def log(msg):
    line = f"{time.strftime('[%m-%d %H:%M:%S]')} {msg}"
    if sys.stdout:  # 打包成 exe 后台运行时没有控制台
        print(line, flush=True)
    try:
        if LOG_FILE.stat().st_size > LOG_MAX:
            os.replace(LOG_FILE, LOG_FILE.with_name("bot.old.log"))
    except OSError:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def is_home(img):
    if connection_lost(img) or find_all(img, TPL["btn_reload"], 0.8):
        return False
    if find_all(img[60:180, 40:160], TPL["event_back"], 0.8):  # 活动广告盖在主界面上
        return False
    return bool(find_all(img, TPL["home_attack"], 0.8) or is_new_home(img))


def is_new_home(img):
    return bool(find_all(img[810:890, 20:190], TPL["new_home_label"], 0.9))


def connection_lost(img):
    """服务连接错误会盖在村庄上；背景的进攻按钮仍然能匹配。"""
    return bool(find_all(img[330:570, 380:1220], TPL["connection_lost"], 0.9))


def game_updating(img):
    return bool(find_all(img[:110, 650:950], TPL["game_update"], 0.9))


def collect(img):
    """点资源气泡收集。哪种仓库满了就不点那种气泡（点了也收不进去，只会弹出建筑菜单）。"""
    count = 0
    names = []
    if not gold_full(img):
        names.append("collect_gold")
    if not elixir_full(img):
        names.append("collect_elixir")
    for name in names:
        for x, y in find_all_scaled(img, TPL[name]):
            tap(jitter(x), jitter(y))
            count += 1
            time.sleep(random.uniform(0.4, 1.0))
    if count:
        tap(*DESELECT)
    return count


def _top_zeros(img):
    """顶部栏里工人图标的位置，和所有"0/x"里那个 0 的位置。
    顶部栏的图标会随右边护盾倒计时的长短整体左右挪，所以不能写死坐标，要以工人图标为准。"""
    bar = img[0:100]
    icon = find_all(bar, TPL["builder_icon"], 0.8)
    zeros = find_all(bar, TPL["builders_zero"], 0.85)
    return (icon[0][0] if icon else None), zeros


def no_free_builder(img):
    """工人是不是都在忙。
    新版本：右下角显示"空闲/总数"，读出来是 0 就是没空闲工人；读不出来也不升级，
    免得点出"花宝石买工人"的窗口。"""
    if is_new_home(img):
        free = free_builders(img)
        return free is None or free == 0
    icon, zeros = _top_zeros(img)
    if icon is None:
        return True
    return any(icon < x < icon + 140 for x, _ in zeros)


def free_builders(img):
    """顶部"空闲工人/总数"里的空闲数，读不出来返回 None。"""
    if is_new_home(img):
        hsv = cv2.cvtColor(img[676:711, 1423:1448], cv2.COLOR_BGR2HSV)
        mask = ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype('uint8')
        return read_mask(mask)
    bar = img[0:100]
    icon = find_all(bar, TPL["builder_icon"], 0.8)
    if not icon:
        return None
    x = icon[0][0]
    hsv = cv2.cvtColor(img[25:78, x + 40:x + 150], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype("uint8")
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    comps = sorted(tuple(st[i]) for i in range(1, n) if 15 <= st[i, 3] <= 30 and st[i, 4] >= 60)
    if not comps:
        return None
    return read_mask(m[:, comps[0][0]:comps[0][0] + comps[0][2]])  # 最左边那个数字就是空闲数


def builders_reserved(img):
    """空闲工人只剩要留给玩家的那几个了（或者一个都没有），不再派活。
    读不出数字时，没设置留工人就按老办法看"0/x"，设置了就保守地当作没有可用的。"""
    if not KEEP_BUILDERS:
        return no_free_builder(img)
    free = free_builders(img)
    return free is None or free <= KEEP_BUILDERS


def lab_busy(img):
    """实验室是不是正在研究。
    新版本：右下角工人旁边显示"研究中/总数"，前面那个数大于 0 就是有实验室在忙。
    读不出来就当作在忙，这轮先不去找实验室。"""
    if is_new_home(img):
        hsv = cv2.cvtColor(img[682:710, 1514:1542], cv2.COLOR_BGR2HSV)
        mask = ((hsv[..., 1] < 70) & (hsv[..., 2] > 200)).astype("uint8")
        n = read_mask(mask)  # "空闲/总数"里的空闲数
        return n is None or n == 0
    icon, zeros = _top_zeros(img)
    if icon is None:
        return True
    return any(icon - 220 < x < icon - 20 for x, _ in zeros)


def _bar_filled(img, y0, y1, h0, h1, s_min, v_min):
    """资源条右端那一小截是不是彩色的：填满了才会一直顶到右端。新版本 UI 的资源条在右上角。"""
    px = cv2.cvtColor(img[y0:y1, 1520:1538], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = (float(x) for x in px.mean(0))
    return h0 <= h <= h1 and s > s_min and v > v_min


def gold_full(img):
    """金币是否满了（右上角金币条填到右端）。"""
    return _bar_filled(img, 40, 60, 18, 32, 120, 150)


def elixir_full(img):
    """圣水是否满了（右上角圣水条填到右端，满时是亮紫色）。"""
    return _bar_filled(img, 98, 122, 130, 168, 100, 150)


def storage_full(img):
    """金币和圣水是否都满了。"""
    return gold_full(img) and elixir_full(img)


def recover(img):
    """不在主界面时先尝试点回营/关窗口。"""
    # 战斗结算的"回营"/"领取奖励"、开宝箱后的"继续"、活动广告的返回箭头
    # 还有游戏内网页（公告等）底部的"返回游戏"
    # 还有"报告首领"村民委托面板的收起箭头（点到走动的村民会弹出来）
    for name in ["btn_return", "btn_claim", "btn_continue", "event_back", "web_back", "btn_panel_collapse"]:
        pts = find_all(img, TPL[name], 0.8)
        if pts:
            tap(*pts[0])
            return True
    # 停在进攻页面（联机/单人模式那个面板，比如单人模式打输后会回到关卡地图）：点右边村庄空地收起
    if find_all(img, TPL["btn_search"], 0.8) or find_all(img, TPL["sp_attack"], 0.8) or find_all(img, TPL["sp_tab"], 0.8):
        tap(1300, 600)
        return True
    return close_windows(find_all, find_all_scaled, TPL, log)


def logged_out(img):
    """停在登录界面，或者已经点进了"QQ 授权登录"扫码页（挂机在登录界面上乱点会点进去）。"""
    return bool(find_all(img, TPL["login_screen"], 0.8) or find_all(img, TPL["login_qq_auth"], 0.8))


def restart_game():
    if logged_out(screenshot()):
        return False  # 在登录界面重启也没用
    log("重启游戏")
    adb("shell", "am", "force-stop", PACKAGE)
    time.sleep(3)
    adb("shell", "monkey", "-p", PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
    for _ in range(40):
        time.sleep(3)
        img = screenshot()
        if logged_out(img):
            raise LoggedOut
        if game_updating(img):
            log("游戏需要更新，交给主循环处理，停止重复重启")
            return False
        if is_home(img):
            log("已重新进入村庄")
            return True
    log("重启后 2 分钟还没进村庄")
    return False


def emulator_online():
    return device() is not None


class LoggedOut(Exception):
    """账号掉线，停在登录界面。"""


_not_home = 0
_connection_failures = 0
_last_update_click = 0
_last_lab = 0
_last_potion = 0
POTION_INTERVAL = 2 * 3600  # 隔多久看一次要不要用研究药水
_th_pause_until = 0         # 游戏建议先升进攻时，这个时间之前不去升大本营
TH_PAUSE = 6 * 3600


def one_round():
    global _not_home, _connection_failures, _last_update_click
    img = screenshot()
    if logged_out(img):
        # 一般是同一个账号在手机上登录，把模拟器挤下线了；登录要本人操作，挂机直接停掉
        raise LoggedOut
    if game_updating(img):
        _not_home = 0
        _connection_failures = 0
        if find_all(img[350:450, 400:1050], TPL["game_update_prompt"], 0.9):
            if time.time() - _last_update_click > 60:
                config.save_debug(img, "游戏更新")
                log("检测到游戏新版本，确认下载；更新期间暂停操作，不重启游戏")
                tap(485, 778)
                _last_update_click = time.time()
        else:
            log("游戏正在检查或下载更新，等待完成，不重启游戏")
        time.sleep(30)
        return
    if connection_lost(img):
        _connection_failures += 1
        config.save_debug(img, "连接中断")
        log(f"与游戏服务器失去连接，第 {_connection_failures} 次恢复；暂停村庄操作")
        if _connection_failures >= 3:
            log("连续重试仍断线，尝试重启游戏；如仍失败请检查网络或服务器状态")
            restart_game()
            _connection_failures = 0
            time.sleep(60)
        else:
            tap(450, 526)  # 已确认的 Android 连接中断弹窗的“重试”，1600x900
            time.sleep(15)
        return
    reload = find_all(img, TPL["btn_reload"], 0.8)
    if reload:  # "还在吗？因为太久没有进行操作，您已断开连接" 半透明盖在村庄上，点"重新载入游戏"
        log("太久没操作被断开了，重新载入游戏")
        tap(*reload[0])
        time.sleep(15)
        return
    if danger_on_screen(find_all, TPL, img):
        log("屏幕上有可能花宝石的窗口，先关掉")
        close_windows(find_all, find_all_scaled, TPL, log)
        return
    if not is_home(img):
        _not_home += 1
        log(f"不在村庄主界面（第 {_not_home} 次）")
        # recover 每次都点到了东西但一直回不去（比如在活动页面里来回绕），点再多也没用，到次数照样重启
        if not recover(img) and _not_home >= MAX_NOT_HOME or _not_home >= MAX_NOT_HOME * 3:
            restart_game()
            _not_home = 0
        time.sleep(5)
        return
    _not_home = 0
    _connection_failures = 0
    if cancel_placement(img, TPL, log):
        time.sleep(1)
        return
    # 每轮先把画面缩到最小：选建筑、点工人菜单时镜头会自动放大，放大了很多东西就认不出来。
    # 缩到最小是固定的，每次都一样；双指捏合也算操作，顺便保持活跃
    pinch_out(2)
    time.sleep(1)
    img = screenshot()
    # 保持活跃：仓库满了以后一轮里可能一下都不点，游戏太久没人碰就会断开连接。
    # 用拖动而不是点击：草地上会有带委托气泡的村民走来走去，点到会弹出"报告首领"面板
    swipe(1500, 600, 1500, 640, 300)
    time.sleep(0.4)
    swipe(1500, 640, 1500, 600, 300)
    time.sleep(0.5)

    n = collect(img)
    log(f"收集了 {n} 个资源" if n else "暂无可收集资源")
    time.sleep(random.uniform(1, 2))
    if builders_reserved(screenshot()):
        log("工人都在忙，跳过升级" + (f"（留了 {KEEP_BUILDERS} 个给陛下）" if KEEP_BUILDERS else ""))
    else:
        skip_gold = False   # 金币留着升大本营，升级时只花圣水
        build_gold = False  # 造新建筑时可以花金币（大本营要求先造齐新建筑）
        global _th_pause_until
        offense_first = time.time() < _th_pause_until
        if TOWNHALL_FIRST and offense_first:
            skip_gold = False  # 游戏建议先升进攻，这段时间大本营反正升不了，金币不用攒，拿去升防御
        elif TOWNHALL_FIRST:
            r = try_townhall(find_all, find_all_scaled, TPL, log)
            full = gold_full(screenshot())
            if r == "warned":
                _th_pause_until = time.time() + TH_PAUSE
                offense_first = True
                log("先把训练营、部落城堡、实验室、兵种研究升上去，6 小时后再试升大本营（这段时间金币不攒，拿去升防御）")
                skip_gold = False
            elif r == "locked":
                log("大本营要先造齐新建筑才能升，先花金币造新建筑")
                build_gold, skip_gold = True, not full
            elif r == "fail":
                if full:
                    log("大本营这轮没升成，金币已经满了，先花金币升别的")
                else:
                    log("大本营这轮没升成（可能被别的条件卡住），金币先留着")
                    skip_gold = True
            elif r == "poor":
                if full:
                    log("金币满了还不够升大本营，先花金币升别的（比如储金罐）")
                else:
                    log("金币还不够升大本营，先攒着")
                    skip_gold = True
            time.sleep(random.uniform(1, 2))
        # 先造新建筑（城墙、防御塔），再升级已有建筑
        built = 0
        while built < 30 and not builders_reserved(screenshot()) and try_build_new(find_all, find_all_scaled, TPL, log, skip_gold and not build_gold):
            built += 1
            time.sleep(random.uniform(0.5, 1.5))
        while True:
            img = screenshot()
            if builders_reserved(img):
                break
            allow_walls = not CFG["walls_only_when_full"] or gold_full(img)
            if not try_upgrade(find_all, find_all_scaled, TPL, log, skip_gold, allow_walls, offense_first):
                break
            time.sleep(random.uniform(1, 2))
    global _last_lab
    if RESEARCH and not lab_busy(screenshot()) and time.time() - _last_lab > LAB_INTERVAL:
        r = try_research(find_all, find_all_scaled, TPL, log)
        _last_lab = 0 if r == "done" else time.time()  # 研究完了马上接着研究；没东西可研究就过一阵再看
        time.sleep(random.uniform(1, 2))
    global _last_potion
    if RESEARCH and lab_busy(screenshot()) and time.time() - _last_potion > POTION_INTERVAL:
        try_potion(find_all, find_all_scaled, TPL, log)  # 研究时间够长才用，见 lab.POTION_HOURS
        _last_potion = time.time()
    if ATTACK:
        img = screenshot()
        if storage_full(img):
            if CFG["practice"] and not practice_mod.resting():
                # 仓库满了联机白打，去单人模式练下兵打法（军队免费，打完自动补满）
                plan, _ = choose_plan(attack_mod.AVAILABLE)
                if fill_army(find_all, TPL, log, plan) is not None:
                    practice_mod.practice(find_all, TPL, log)
            else:
                log("金币和圣水都满了，这轮不进攻")
        else:
            plan, why = choose_plan(attack_mod.AVAILABLE)  # 自动配兵：几套方案轮流试，挑抢得最多的
            log(f"这场用\"{plan}\"配兵（{why}）")
            plan = fill_army(find_all, TPL, log, plan)  # 方案里的兵种没有时会换一套
            if plan is not None:
                attack(find_all, TPL, log, need_gold=not gold_full(img), need_elixir=not elixir_full(img), plan=plan)

    wait = random.randint(LOOP_MIN, LOOP_MAX)
    log(f"等待 {wait} 秒")
    time.sleep(wait)


def main():
    log("脚本启动")
    size_ok = False
    while True:
        if not ADB:
            log(f"找不到雷电模拟器的 adb.exe，请在控制面板的\"设置\"里填写。{ERROR_WAIT} 秒后重试")
            time.sleep(ERROR_WAIT)
            continue
        if not emulator_online():
            log(f"连不上模拟器，请确认雷电模拟器已打开。{ERROR_WAIT} 秒后重试")
            size_ok = False
            time.sleep(ERROR_WAIT)
            continue
        if not size_ok:
            size = screen_size()
            if size and size != (1600, 900):
                log(f"模拟器分辨率是 {size[0]}x{size[1]}，必须改成 1600x900（雷电设置→性能设置→分辨率），"
                    f"否则什么都认不出来。{ERROR_WAIT} 秒后再查")
                time.sleep(ERROR_WAIT)
                continue
            size_ok = size is not None
        try:
            one_round()
        except LoggedOut:
            log("账号已掉线（停在登录界面，可能手机上登录了同一个账号），挂机已自动停止。登录回来后在控制面板点\"启动挂机\"")
            return
        except Exception:  # 任何意外都不让脚本退出，记下来等一会儿再试
            log(f"出错了，{ERROR_WAIT} 秒后重试：\n{traceback.format_exc()}")
            time.sleep(ERROR_WAIT)


def run():
    """入口：防重复启动、记下进程号，然后一直挂机。"""
    with config.bot_lock() as acquired:
        if not acquired:
            log("已经有一个挂机在运行，这个不启动了。要重开请先在控制面板点\"停止\"")
            raise SystemExit(1)
        _run_locked()


def _run_locked():
    # 兼容还没使用文件锁的旧版本；检查和写入都在锁内执行。
    other = running_pid()
    if other:
        log(f"已经有一个挂机在运行（进程 {other}），这个不启动了。要重开请先在控制面板点\"停止\"")
        raise SystemExit(1)
    PID_FILE.write_text(str(os.getpid()))
    try:
        main()
    except KeyboardInterrupt:
        log("已停止")
    finally:
        try:
            if PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    run()
