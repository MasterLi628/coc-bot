"""配置：读写 config.json（在控制面板的"设置"页改），没设置的项用默认值。"""
import json
import os
import string
import sys
from contextlib import contextmanager
from pathlib import Path

APP_NAME = "部落冲突挂机"                # 打包后 exe 的名字
FROZEN = getattr(sys, "frozen", False)   # 打包成 exe 运行时为 True
BASE = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent  # 模板、设置、日志都放这里（源码版是项目根目录）
TEMPLATES = BASE / "templates"
CONFIG_FILE = BASE / "config.json"
NO_WINDOW = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)  # 调 adb 时不弹黑框

DEFAULTS = {
    "adb": "",                 # 雷电的 adb.exe；空着就自动查找
    "device": "",              # 模拟器编号，如 emulator-5556；空着就用在线的那台
    "attack": True,            # 是否自动进攻
    "townhall_first": True,    # 大本营优先
    "loop_min": 20,            # 每轮间隔秒数（随机）
    "loop_max": 60,
    "max_not_home": 3,         # 连续几轮不在主界面就重启游戏
    "min_gold": 40000,         # 金币没满时，对手金币达到这个数就打
    "min_elixir": 40000,       # 圣水没满时，对手圣水达到这个数就打（两种都不够就换下一个）
    "min_dark_elixir": 250,    # 重油没满时，对手重油达到这个数就打
    "max_skips": 20,           # 最多换几个对手（换一次只花几十到一两百金币，比打穷对手划算）
    "groups": 3,               # 兵分几个方向下
    "upgrade_rule": "cheap",   # 升级规则：cheap＝均衡（先升最便宜的，也就是等级最低的）；priority＝按优先级列表
    "priority": [],            # 升级优先级（建筑代号，见 BUILDINGS）；空着用 DEFAULT_PRIORITY
    "walls_only_when_full": True,  # 城墙只在金币存满时才升
    "keep_builders": 0,        # 留几个工人不用（给玩家自己手动升级，比如升城墙）
    "research": True,
    "practice": True,          # 仓库满了时去单人模式练兵（打下一关，比较几种下兵打法）          # 实验室自动研究（先研究最便宜的，也就是等级最低的兵种）
    "multiplayer_practice": True,  # 满仓时限量进行普通多人练兵
    "close_action": "ask",     # 点控制面板的 ✕：ask＝每次问；tray＝退到后台；quit＝停止挂机并关闭
    "appearance": "system",    # 控制面板配色：system＝跟随系统；light＝浅色；dark＝深色
}
UPGRADE_RULES = {"cheap": "均衡：先升最便宜的（等级低的建筑先升）", "priority": "按下面的优先级列表"}

# 工人菜单里的建筑：代号 → 名字。代号对应 templates/names/代号.png（菜单里的名字截图）
BUILDINGS = {
    "goldsto": "储金罐", "esto": "圣水瓶",
    "barracks": "兵营", "camp": "训练营", "lab": "实验室", "spell": "法术工厂",
    "mortar": "迫击炮", "wizard": "法师塔", "airdef": "防空火箭", "archer": "箭塔", "cannon": "加农炮",
    "tesla": "特斯拉电磁塔", "airsweeper": "空气炮",
    "bomb": "隐形炸弹", "spring": "隐形弹簧", "giantbomb": "巨型炸弹", "airbomb": "空中炸弹", "airmine": "搜空地雷",
    "mine": "金矿", "ecol": "圣水收集器", "darkdrill": "暗黑重油钻井", "darksto": "暗黑重油罐",
    "cc": "部落城堡", "herohall": "英雄殿堂", "king": "野蛮人之王",
    "other": "其他（认不出名字的建筑）",
    "wall": "城墙",
}
DEFAULT_PRIORITY = list(BUILDINGS)
# 造新建筑时最后才造的资源建筑（用户要求：升本后先把防御建筑造齐，再造资源建筑）
RESOURCE = {"goldsto", "esto", "mine", "ecol", "darkdrill", "darksto"}
NEVER_UPGRADE = {"boat", "hut", "townhall"}  # 帆船（会去建筑大师基地）、建筑工人小屋（要宝石）；大本营单独处理（"大本营优先"）


def priority(cfg=None):
    """升级优先级：用户排过的顺序在前，新加的建筑按默认顺序补在后面。"""
    saved = [k for k in (cfg or CFG)["priority"] if k in BUILDINGS]
    return saved + [k for k in DEFAULT_PRIORITY if k not in saved]


def load():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _registry_dirs():
    try:
        import winreg
    except ImportError:
        return []
    dirs = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key in (r"Software\leidian\ldplayer9", r"Software\leidian\ldplayer"):
            try:
                with winreg.OpenKey(root, key) as k:
                    dirs.append(winreg.QueryValueEx(k, "InstallDir")[0])
            except OSError:
                pass
    return dirs


def find_adb():
    """自动找雷电模拟器的 adb.exe，找不到返回空字符串。"""
    candidates = _registry_dirs()
    for drive in string.ascii_uppercase:
        for sub in (r"leidian\LDPlayer9", r"LDPlayer\LDPlayer9", r"Program Files\leidian\LDPlayer9",
                    r"Program Files\LDPlayer\LDPlayer9"):
            candidates.append(f"{drive}:\\{sub}")
    for d in candidates:
        p = os.path.join(d, "adb.exe")
        if os.path.isfile(p):
            return p
    return ""


# 放在用户目录里，源码版和打包版共用，两边不会同时挂机
PID_FILE = Path(os.environ.get("LOCALAPPDATA") or BASE) / "coc-bot" / "bot.pid"
PID_FILE.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def bot_lock(path=None):
    """原子地抢占挂机锁；强制结束进程时 Windows 也会自动释放。

    锁文件独立于 PID 文件，避免面板读取 PID 或退出时删 PID 影响锁。
    源码和打包版共用同一个位置，锁必须持有到整个挂机循环结束。
    """
    import msvcrt
    path = path if path is not None else PID_FILE.with_suffix(".lock")
    with open(path, "a+b") as f:
        f.seek(0, os.SEEK_END)
        if not f.tell():
            f.write(b"\0")
            f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno not in (13, 36):  # EACCES / EDEADLK：已被其他进程占用
                raise
            yield False
            return
        try:
            yield True
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _pid_alive(pid):
    """pid 是不是一个还在运行的 python 进程（防止 pid 被别的程序复用）。"""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        name = os.path.basename(buf.value).lower()
        return name.startswith("python") or name.startswith(APP_NAME) or name == os.path.basename(sys.executable).lower()
    finally:
        k32.CloseHandle(h)


def running_pid():
    """正在运行的挂机的进程号，没有在运行返回 None。"""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() and _pid_alive(pid) else None


def imread(path, flags=None):
    """cv2.imread 读不了带中文的路径，改用 numpy 读文件再解码。"""
    import cv2
    import numpy as np
    data = np.fromfile(str(path), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags) if data.size else None


def save_debug(img, name):
    """出问题时存一张截图到 debug 文件夹（同名覆盖，不会越存越多）。"""
    import cv2
    try:
        d = BASE / "debug"
        d.mkdir(exist_ok=True)
        cv2.imencode(".png", img)[1].tofile(str(d / f"{name}.png"))
    except Exception:
        pass


def adb_path(cfg=None):
    p = (cfg or load())["adb"]
    return p if p and os.path.isfile(p) else find_adb()


CFG = load()
