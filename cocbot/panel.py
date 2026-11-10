"""挂机控制面板（打包版 exe 的界面）：启动/停止挂机、看战果和日志、改设置。

挂机在后台单独运行，面板只是看和控制；关掉面板可以退到托盘，挂机照常跑。
界面用 CustomTkinter：左边菜单（总览 / 日志 / 设置），右边内容，顶部是状态栏和启动/停止按钮。
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import config, notifier
try:  # 托盘图标（pip install pystray pillow）；没装就退回普通的最小化
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None

HERE = config.BASE
LOG_FILE = HERE / "bot.log"
ASSETS = HERE / "assets"   # 图标（由 assets/make_assets.py 生成）
AUTO = "自动（用在线的那台）"
NO_WINDOW = config.NO_WINDOW
MAX_LINES = 3000          # 日志最多保留多少行
TAIL_BYTES = 60000        # 打开面板时先读 bot.log 最后这么多内容
EVENT_COUNT = 9           # 总览页"最近动态"显示几条

CLOSE_ACTIONS = {"ask": "每次都问", "tray": "退到后台（挂机继续）", "quit": "关闭脚本（停止挂机并退出）"}
APPEARANCES = {"system": "跟随系统", "light": "浅色", "dark": "深色"}

# 设置页的数字项：(键, 名称, 说明)
ATTACK_NUMBERS = [
    ("min_gold", "对手金币至少多少才打", "金币存满了就不看这一项"),
    ("min_elixir", "对手圣水至少多少才打", "圣水存满了就不看这一项；两种都不够就换下一个对手"),
    ("max_skips", "最多换几个对手", "每换一次只花一点金币，换满了就将就打"),
    ("groups", "兵分几路", "从几个方向同时下兵"),
]
LOOP_NUMBERS = [
    ("loop_min", "每轮最少等（秒）", "一轮做完后随机等一会儿再开始下一轮"),
    ("loop_max", "每轮最多等（秒）", ""),
    ("max_not_home", "几轮回不到村庄就重启游戏", "卡在别的界面时，连续这么多轮就重启游戏"),
]
BUILDER_NUMBERS = [
    ("keep_builders", "留几个工人不用", "留给自己手动升级（比如城墙），挂机不会占用；0＝全部用上"),
]
NUMBERS = ATTACK_NUMBERS + LOOP_NUMBERS + BUILDER_NUMBERS

# ---------- 配色（浅色, 深色） ----------
FONT = "Microsoft YaHei UI"
BG = ("#F4F5F7", "#0B1220")
SIDEBAR = ("#FFFFFF", "#0F172A")
CARD = ("#FFFFFF", "#111C2E")
BORDER = ("#E4E6EB", "#233149")
TEXT = ("#1F2329", "#E7EDF7")
MUTED = ("#6B7280", "#9AAAC0")
ACCENT = ("#D98A00", "#F2A93B")          # 金币黄
ACCENT_HOVER = ("#B97500", "#D99428")
ACCENT_SOFT = ("#FCF0D9", "#3A2F1C")
GREEN = ("#1E9E55", "#3CCB72")
GREEN_HOVER = ("#178246", "#30AD5F")
GREEN_SOFT = ("#E2F6EA", "#183224")
RED = ("#D64545", "#F06B6B")
RED_SOFT = ("#FCE7E7", "#3A1F22")
AMBER_SOFT = ("#FFF4DB", "#3A321C")
GRAY_SOFT = ("#EEF0F3", "#2A2E36")
SWITCH_OFF = ("#C9CDD4", "#4A505B")
ELIXIR = ("#B646C9", "#D17BE0")
# 日志框不管浅色深色都用深底（像终端），颜色只要一套
LOG_BG, LOG_FG = "#09101D", "#D8E1EF"
LOG_TAGS = {"err": "#F07070", "warn": "#F0B060", "good": "#7FD69A", "dim": "#7A808A"}


def run_adb(adb, *args):
    try:
        return subprocess.run([adb, *args], capture_output=True, text=True, timeout=15,
                              creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def bot_command():
    """启动挂机的命令：打包版用 exe 自己加 --bot；源码版用 python.exe（不是 pythonw.exe）跑 app.py --bot。"""
    if config.FROZEN:
        return [sys.executable, "--bot"]
    p = Path(sys.executable).with_name("python.exe")
    return [str(p) if p.is_file() else sys.executable, "-u", str(HERE / "app.py"), "--bot"]


def tray_image(running):
    """托盘小图标：软件图标右下角加个圆点，运行中是绿点，停止是灰点。"""
    try:
        img = Image.open(ASSETS / "icon.png").convert("RGBA").resize((64, 64), Image.LANCZOS)
    except OSError:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((4, 4, 60, 60), fill=(235, 170, 40))
    d = ImageDraw.Draw(img)
    d.ellipse((38, 38, 63, 63), fill=(255, 255, 255))
    d.ellipse((42, 42, 59, 59), fill=(40, 190, 70) if running else (150, 150, 150))
    return img


def write_log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('[%m-%d %H:%M:%S]')} {msg}\n")
    except OSError:
        pass


def line_tag(line):
    """日志行的颜色：err 出错 / warn 要注意 / good 做成了一件事 / "" 普通。"""
    if any(k in line for k in ("出错", "Traceback", "Error", "连不上", "找不到", "掉线")):
        return "err"
    if any(k in line for k in ("宝石", "分辨率", "重启", "没找到", "放弃", "不在村庄", "建议先升进攻", "新兵种")):
        return "warn"
    if any(k in line for k in ("开打", "本场抢到", "已开始", "已建造", "启动", "已重新进入")):
        return "good"
    return ""


EVENT_KEYS = ("本场抢到", "已开始升级", "已开始研究", "已建造", "账号已掉线", "出错了", "重启游戏", "建议先升进攻",
              "发现新兵种")


def split_time(line):
    """'[10-16 17:27:51] 内容' → ('17:27', '内容')；没有时间戳返回 ('', 原文)。"""
    if line.startswith("[") and "] " in line[:20]:
        stamp, text = line[1:].split("] ", 1)
        return stamp.split(" ")[-1][:5], text
    return "", line


def latest_log_text(line, limit=46):
    """Fit the status-bar preview without letting long logs collide with actions."""
    t, message = split_time(line)
    prefix = f"最新：{t}  " if t else ""
    if len(message) > limit:
        message = message[:max(0, limit)] + "…"
    return prefix + message


def event_text(text):
    """总览"最近动态"里的一行，把长句子缩短。"""
    m = re.match(r"本场抢到 金币 (\d+) / 圣水 (\d+)（对手的 (\d+%)）(?:，配兵：(.+))?", text)
    if m:
        g, e, ratio, plan = m.groups()
        return f"进攻抢到 {wan(int(g))} 金币 / {wan(int(e))} 圣水（{ratio}）" + (f" · {plan}" if plan else "")
    if text.startswith("游戏建议先升进攻"):
        return "游戏建议先升进攻建筑，暂缓升大本营"
    text = text.replace("实验室：已开始研究", "开始研究 ").replace("已开始升级", "开始升级 ")
    return text if len(text) <= 32 else text[:31] + "…"


_IMG_CACHE = {}


def ui_icon(name, color=TEXT, size=18):
    """界面线条图标（白色图按颜色上色），color 可以是 (浅色, 深色)。找不到图返回 None。"""
    key = (name, color, size)
    if key not in _IMG_CACHE:
        try:
            from PIL import Image as PILImage
            alpha = PILImage.open(ASSETS / "ui" / f"{name}.png").split()[3]
        except Exception:
            _IMG_CACHE[key] = None
            return None

        def tint(c):
            c = c.lstrip("#")
            im = PILImage.new("RGBA", alpha.size, tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (0,))
            im.putalpha(alpha)
            return im
        light, dark = (color, color) if isinstance(color, str) else color
        _IMG_CACHE[key] = ctk.CTkImage(light_image=tint(light), dark_image=tint(dark), size=(size, size))
    return _IMG_CACHE[key]


def game_icon(name, size=40):
    """游戏风格的彩色小图（金币、圣水、双剑、星星；logo 是软件图标）。"""
    key = ("game", name, size)
    if key not in _IMG_CACHE:
        try:
            from PIL import Image as PILImage
            im = PILImage.open(ASSETS / ("icon.png" if name == "logo" else f"game/{name}.png"))
            _IMG_CACHE[key] = ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))
        except Exception:
            _IMG_CACHE[key] = None
    return _IMG_CACHE[key]


def wan(n):
    """数字写成"12.3万"这种。"""
    return f"{n / 10000:.1f}万" if n >= 10000 else f"{n:,}"


def duration(sec):
    sec = int(sec)
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h}小时{m}分" if h else f"{m}分钟"


class App(ctk.CTk):
    def __init__(self):
        cfg = config.load()
        ctk.set_appearance_mode(cfg.get("appearance", "system"))
        super().__init__(fg_color=BG)
        self.title("部落冲突挂机")
        self.geometry("1100x720")
        self.minsize(940, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_x)
        self._set_icon()

        self.f_title = ctk.CTkFont(FONT, 20, "bold")
        self.f_h2 = ctk.CTkFont(FONT, 15, "bold")
        self.f_body = ctk.CTkFont(FONT, 13)
        self.f_small = ctk.CTkFont(FONT, 12)
        self.f_metric = ctk.CTkFont(FONT, 26, "bold")
        self.f_mono = ctk.CTkFont("Consolas", 12)

        self.log_pos = 0
        self.lines = deque(maxlen=MAX_LINES)   # 读到的全部日志行
        self.last_line = ""
        self.was_running = None
        self.starting_until = 0
        self.tray = None
        self.tray_running = None
        self.tray_tipped = False
        self.tray_cmds = queue.Queue()  # 托盘菜单在别的线程里，命令通过队列交给界面线程执行
        self.stats_tick = 0
        self.events_sig = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.build_sidebar()
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(0, 20), pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self.build_header(main)
        self.pages = {}
        holder = ctk.CTkFrame(main, fg_color="transparent")
        holder.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        for name, build in (("overview", self.build_overview), ("log", self.build_log), ("settings", self.build_settings)):
            page = ctk.CTkFrame(holder, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew")
            build(page, cfg)
            self.pages[name] = page
        self.show_page("overview")

        self.load_log_tail()
        self.poll()
        self.after(300, self.detect)
        if pystray:
            self.start_tray()
            self.poll_tray()

    def _set_icon(self, win=None):
        """窗口左上角和任务栏的图标。CTk 启动后会设自己的图标，所以晚一点再覆盖。"""
        ico = ASSETS / "icon.ico"
        if not ico.is_file():
            return
        w = win or self

        def apply():
            w.iconbitmap(str(ico))
            # 任务栏用的是大图标，iconbitmap 不一定设上，Windows 就会去用缓存里的旧 exe 图标，这里手动把大小图标都设上
            try:
                import ctypes
                u = ctypes.windll.user32
                hwnd = u.GetParent(w.winfo_id())
                for which, size in ((1, 48), (0, 16)):  # ICON_BIG / ICON_SMALL
                    h = u.LoadImageW(None, str(ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                    if h:
                        u.SendMessageW(hwnd, 0x80, which, h)  # WM_SETICON
            except Exception:
                pass
        w.after(250, apply)

    # ================= 通用小部件 =================
    def card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER, **kw)

    def label(self, parent, text="", font=None, color=TEXT, **kw):
        return ctk.CTkLabel(parent, text=text, font=font or self.f_body, text_color=color, **kw)

    def button(self, parent, text, command, primary=False, icon=None, **kw):
        if primary:
            style = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF")
        else:
            style = dict(fg_color="transparent", hover_color=GRAY_SOFT, text_color=TEXT, border_width=1,
                         border_color=BORDER)
        if icon:
            color = "#FFFFFF" if primary else kw.get("text_color", TEXT)
            style.update(image=ui_icon(icon, color, 16), compound="left")
        style.update(font=self.f_body, corner_radius=8, height=34)
        style.update(kw)
        return ctk.CTkButton(parent, text=text, command=command, **style)

    def segmented(self, parent, values, command):
        return ctk.CTkSegmentedButton(parent, values=values, font=self.f_small, command=command, height=30,
                                      fg_color=GRAY_SOFT, unselected_color=GRAY_SOFT, unselected_hover_color=BORDER,
                                      selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                      text_color=(TEXT[0], "#FFFFFF"))

    def switch(self, parent, variable, **kw):
        """开关。白色圆钮要整个落在轨道里（加一圈和轨道同色的边），浅色背景上才看得见。"""
        sw = ctk.CTkSwitch(parent, variable=variable, progress_color=ACCENT, fg_color=SWITCH_OFF, button_color="#FFFFFF",
                           button_hover_color="#F2F2F2", border_width=2, font=self.f_small, text_color=TEXT, **kw)
        recolor = lambda *_: sw.configure(border_color=ACCENT if variable.get() else SWITCH_OFF)
        variable.trace_add("write", recolor)
        recolor()
        return sw

    # ================= 左侧菜单 =================
    def build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=210, border_width=0)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_rowconfigure(5, weight=1)

        logo = ctk.CTkFrame(bar, fg_color="transparent")
        logo.grid(row=0, column=0, sticky="ew", padx=20, pady=(26, 30))
        ctk.CTkLabel(logo, text="", image=game_icon("logo", 44)).pack(side="left")
        names = ctk.CTkFrame(logo, fg_color="transparent")
        names.pack(side="left", padx=(8, 0))
        self.label(names, "部落冲突挂机", self.f_h2).pack(anchor="w")
        self.label(names, "雷电模拟器 · 自动发育", self.f_small, MUTED).pack(anchor="w")

        self.nav = {}
        for i, (key, text) in enumerate((("overview", "总览"), ("log", "运行日志"), ("settings", "设置"))):
            b = ctk.CTkButton(bar, text=text, anchor="w", height=42, corner_radius=8, font=self.f_body,
                              fg_color="transparent", hover_color=GRAY_SOFT, text_color=TEXT,
                              image=ui_icon(key, MUTED), compound="left",
                              command=lambda k=key: self.show_page(k))
            b.grid(row=1 + i, column=0, sticky="ew", padx=14, pady=2)
            self.nav[key] = b

        foot = ctk.CTkFrame(bar, fg_color="transparent")
        foot.grid(row=6, column=0, sticky="ew", padx=16, pady=20)
        self.label(foot, "配色", self.f_small, MUTED).pack(anchor="w", pady=(0, 6))
        self.appearance = self.segmented(foot, list(APPEARANCES.values()), self.change_appearance)
        self.appearance.set(APPEARANCES.get(config.load().get("appearance"), APPEARANCES["system"]))
        self.appearance.pack(fill="x")

    def show_page(self, key):
        self.pages[key].tkraise()
        for k, b in self.nav.items():
            on = k == key
            b.configure(fg_color=ACCENT_SOFT if on else "transparent", text_color=ACCENT if on else TEXT,
                        font=ctk.CTkFont(FONT, 13, "bold") if on else self.f_body,
                        image=ui_icon(k, ACCENT if on else MUTED))

    def change_appearance(self, text):
        key = next((k for k, v in APPEARANCES.items() if v == text), "system")
        ctk.set_appearance_mode(key)
        cfg = config.load()
        cfg["appearance"] = key
        config.save(cfg)

    # ================= 顶部状态栏 =================
    def build_header(self, parent):
        head = self.card(parent)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        self.pill = ctk.CTkLabel(head, text="", font=ctk.CTkFont(FONT, 13, "bold"), corner_radius=14, height=30,
                                 width=96)
        self.pill.grid(row=0, column=0, rowspan=2, padx=(18, 14), pady=18)
        self.state_text = self.label(head, "", self.f_h2, anchor="w")
        self.state_text.grid(row=0, column=1, sticky="ew", pady=(16, 0))
        self.doing = self.label(head, "", self.f_small, MUTED, anchor="w")
        self.doing.grid(row=1, column=1, sticky="ew", pady=(0, 16))
        self.toggle_btn = ctk.CTkButton(head, text="", width=150, height=42, corner_radius=10, compound="left",
                                        font=ctk.CTkFont(FONT, 14, "bold"), command=self.toggle)
        self.toggle_btn.grid(row=0, column=2, rowspan=2, padx=18)

    def toggle(self):
        self.stop() if config.running_pid() else self.start()

    def update_header(self, pid):
        if pid:
            try:
                up = duration(time.time() - config.PID_FILE.stat().st_mtime)
            except OSError:
                up = "?"
            self.pill.configure(text="运行中", fg_color=GREEN_SOFT, text_color=GREEN)
            self.state_text.configure(text=f"挂机运行中 · 已运行 {up}")
            self.toggle_btn.configure(text="停止挂机", fg_color=RED_SOFT, hover_color=RED_SOFT, text_color=RED,
                                      border_width=1, border_color=RED, image=ui_icon("stop", RED, 16))
        elif time.time() < self.starting_until:
            self.pill.configure(text="启动中", fg_color=AMBER_SOFT, text_color=ACCENT)
            self.state_text.configure(text="正在启动挂机…")
            self.toggle_btn.configure(text="启动中…", fg_color=GRAY_SOFT, hover_color=GRAY_SOFT, text_color=MUTED,
                                      border_width=0, image=ui_icon("clock", MUTED, 16))
        else:
            self.pill.configure(text="已停止", fg_color=GRAY_SOFT, text_color=MUTED)
            self.state_text.configure(text="挂机没有在运行")
            self.toggle_btn.configure(text="启动挂机", fg_color=GREEN, hover_color=GREEN_HOVER,
                                      text_color="#FFFFFF", border_width=0, image=ui_icon("play", "#FFFFFF", 16))
        last = next((l for l in reversed(self.lines) if split_time(l)[0]), "")
        self.doing.configure(text=latest_log_text(last) if last else "还没有日志")

    # ================= 总览页 =================
    def build_overview(self, page, cfg):
        for i in range(4):
            page.grid_columnconfigure(i, weight=1, uniform="metric")
        page.grid_rowconfigure(2, weight=1)
        self.label(page, "今日战果", self.f_h2).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.label(page, f"{time.strftime('%Y年%m月%d日')}  ·  按战斗记录日期统计", self.f_small, MUTED).grid(
            row=0, column=1, columnspan=3, sticky="e", pady=(0, 10))
        self.metrics = {}
        for i, (key, name, color, pic) in enumerate((
                ("场数", "进攻场数", TEXT, "battle"), ("金币", "抢到金币", ACCENT, "gold"),
                ("圣水", "抢到圣水", ELIXIR, "elixir"), ("平均比例", "平均掠夺率", GREEN, "star"))):
            c = self.card(page)
            c.grid(row=1, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0 if i == 3 else 8))
            c.grid_columnconfigure(0, weight=1)
            self.label(c, name, self.f_small, MUTED).grid(row=0, column=0, sticky="w", padx=(18, 0), pady=(16, 0))
            v = self.label(c, "—", self.f_metric, color)
            v.grid(row=1, column=0, sticky="w", padx=(18, 0), pady=(2, 16))
            ctk.CTkLabel(c, text="", image=game_icon(pic, 34)).grid(row=0, column=1, rowspan=2, padx=(0, 16))
            self.metrics[key] = v

        plans = self.card(page)
        plans.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(0, 8), pady=(16, 0))
        self.label(plans, "配兵成绩", self.f_h2).pack(anchor="w", padx=18, pady=(16, 0))
        self.label(plans, "最近 20 场战利品平均占比 · 脚本会更多选择成绩好的配兵", self.f_small, MUTED).pack(
            anchor="w", padx=18, pady=(2, 10))
        self.plan_box = ctk.CTkFrame(plans, fg_color="transparent")
        self.plan_box.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.plan_rows = {}

        ev = self.card(page)
        ev.grid(row=2, column=2, columnspan=2, sticky="nsew", padx=(8, 0), pady=(16, 0))
        ev_head = ctk.CTkFrame(ev, fg_color="transparent")
        ev_head.pack(fill="x", padx=18, pady=(12, 0))
        self.label(ev_head, "最近动态", self.f_h2).pack(side="left", anchor="w")
        self.button(ev_head, "完整日志 →", self.show_recent_logs, width=112, height=28,
                    font=self.f_small).pack(side="right")
        self.label(ev, "进攻、升级和需要留意的事件", self.f_small, MUTED).pack(anchor="w", padx=18, pady=(2, 10))
        self.event_box = ctk.CTkFrame(ev, fg_color="transparent")
        self.event_box.pack(fill="both", expand=True, padx=18, pady=(0, 16))

    def show_recent_logs(self):
        self.show_page("log")

    def refresh_stats(self):
        try:
            from . import stats
            d = stats.summary()
        except Exception:
            return
        self.metrics["场数"].configure(text=str(d["场数"]))
        self.metrics["金币"].configure(text=wan(d["金币"]))
        self.metrics["圣水"].configure(text=wan(d["圣水"]))
        self.metrics["平均比例"].configure(text=f"{d['平均比例']:.0%}" if d["场数"] else "—")
        try:
            from . import stats
            scores = stats.plan_scores()
        except Exception:
            return
        best = max(scores, key=lambda k: scores[k][0]) if any(n for _, n in scores.values()) else None
        for r, (name, (avg, n)) in enumerate(sorted(scores.items(), key=lambda t: -t[1][0])):
            if name not in self.plan_rows:
                row = ctk.CTkFrame(self.plan_box, fg_color="transparent")
                row.grid_columnconfigure(1, weight=1)
                title = self.label(row, name, self.f_body, width=96, anchor="w")
                title.grid(row=0, column=0, sticky="w")
                bar = ctk.CTkProgressBar(row, height=10, corner_radius=5, progress_color=ACCENT, fg_color=GRAY_SOFT)
                bar.grid(row=0, column=1, sticky="ew", padx=10)
                val = self.label(row, "", self.f_small, MUTED, width=96, anchor="e")
                val.grid(row=0, column=2, sticky="e")
                self.plan_rows[name] = (row, title, bar, val)
            row, title, bar, val = self.plan_rows[name]
            row.grid(row=r, column=0, sticky="ew", pady=7)
            self.plan_box.grid_columnconfigure(0, weight=1)
            bar.set(avg if n else 0)
            bar.configure(progress_color=GREEN if name == best else ACCENT)
            title.configure(text=name + ("  " if name == best else ""), compound="right",
                            image=game_icon("star", 16) if name == best else None)
            val.configure(text=f"{avg:.0%} · {n} 场" if n else "还没打过")

    def refresh_events(self):
        events = [l for l in self.lines if split_time(l)[0] and any(k in l for k in EVENT_KEYS)][-EVENT_COUNT:]
        sig = tuple(events)
        if sig == self.events_sig:
            return
        self.events_sig = sig
        for w in self.event_box.winfo_children():
            w.destroy()
        if not events:
            self.label(self.event_box, "还没有动态", self.f_small, MUTED).pack(anchor="w")
            return
        for line in reversed(events):
            t, text = split_time(line)
            row = ctk.CTkFrame(self.event_box, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text="", image=self.event_icon(text), width=20).pack(side="left")
            self.label(row, t, self.f_small, MUTED, width=44, anchor="w").pack(side="left", padx=(4, 6))
            self.label(row, event_text(text), self.f_small, anchor="w").pack(
                side="left", fill="x", expand=True)

    def event_icon(self, text):
        if "本场抢到" in text:
            return game_icon("gold", 18)
        if "研究" in text:
            return ui_icon("bulb", ELIXIR, 16)
        if "升级" in text or "建造" in text:
            return ui_icon("wrench", GREEN, 16)
        if "出错" in text or "掉线" in text:
            return ui_icon("error", RED, 16)
        if "重启" in text:
            return ui_icon("reset", ACCENT, 16)
        return ui_icon("warn", ACCENT, 16)

    # ================= 日志页 =================
    def build_log(self, page, cfg):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.log_filter = self.segmented(bar, ["全部", "只看重要的"], lambda _: self.render_log())
        self.log_filter.set("全部")
        self.log_filter.pack(side="left")
        self.search = ctk.CTkEntry(bar, placeholder_text="搜索日志…", width=220, height=32, font=self.f_small,
                                   corner_radius=8, border_color=BORDER)
        self.search.pack(side="left", padx=12)
        self.search.bind("<KeyRelease>", lambda e: self.render_log())
        self.button(bar, "清空显示", self.clear_log, icon="clear", height=32).pack(side="right")
        self.follow = tk.BooleanVar(value=True)
        self.switch(bar, text="自动滚到最新", variable=self.follow).pack(side="right", padx=12)

        box = ctk.CTkFrame(page, fg_color=LOG_BG, corner_radius=12)
        box.grid(row=1, column=0, sticky="nsew")
        self.text = ctk.CTkTextbox(box, fg_color=LOG_BG, text_color=LOG_FG, font=self.f_mono, wrap="none",
                                   corner_radius=12, activate_scrollbars=True)
        self.text.pack(fill="both", expand=True, padx=4, pady=4)
        for tag, color in LOG_TAGS.items():
            self.text.tag_config(tag, foreground=color)
        self.text.bind("<Key>", lambda e: None if e.state & 4 else "break")  # 只读，但能 Ctrl+C 复制
        self.label(page, f"日志同时保存在 {LOG_FILE}", self.f_small, MUTED).grid(row=2, column=0, sticky="w",
                                                                                pady=(8, 0))

    def log_visible(self, line):
        if self.log_filter.get() == "只看重要的" and not line_tag(line):
            return False
        q = self.search.get().strip()
        return not q or q in line

    def insert_lines(self, lines):
        for line in lines:
            if self.log_visible(line):
                t, text = split_time(line)
                if t:
                    self.text.insert("end", line[:line.index("]") + 1] + " ", ("dim",))
                    self.text.insert("end", text + "\n", (line_tag(line),) if line_tag(line) else ())
                else:
                    self.text.insert("end", line + "\n", (line_tag(line),) if line_tag(line) else ())

    def render_log(self):
        self.text.delete("1.0", "end")
        self.insert_lines(self.lines)
        self.text.see("end")

    def clear_log(self):
        self.lines.clear()
        self.text.delete("1.0", "end")

    def append(self, chunk):
        new = chunk.splitlines()
        if not new:
            return
        if any(l.strip() for l in new):
            self.last_line = next(l for l in reversed(new) if l.strip())
        at_bottom = self.text.yview()[1] > 0.999
        overflow = len(self.lines) + len(new) > MAX_LINES
        self.lines.extend(new)
        if overflow:
            self.render_log()
        else:
            self.insert_lines(new)
        if self.follow.get() or at_bottom:
            self.text.see("end")

    def read_log(self, start):
        """从 start 字节读到最后一个完整行，返回 (文本, 新位置)。"""
        with open(LOG_FILE, "rb") as f:
            f.seek(start)
            data = f.read()
        cut = data.rfind(b"\n") + 1
        return data[:cut].decode("utf-8", "replace"), start + cut

    def load_log_tail(self):
        try:
            size = LOG_FILE.stat().st_size
        except OSError:
            return
        start = max(0, size - TAIL_BYTES)
        text, self.log_pos = self.read_log(start)
        if start:
            text = text.split("\n", 1)[-1]  # 第一行可能不完整
        self.append(text)

    # ================= 定时刷新 =================
    def poll(self):
        try:
            size = LOG_FILE.stat().st_size
            if size < self.log_pos:  # 日志被删了或清空了
                self.log_pos = 0
            if size > self.log_pos:
                text, self.log_pos = self.read_log(self.log_pos)
                self.append(text)
                if "发现新兵种" in text:
                    self.notify_new_troop()
        except OSError:
            pass
        pid = config.running_pid()
        if pid:
            self.starting_until = 0
        self.update_header(pid)
        self.update_tray(bool(pid))
        if self.stats_tick % 10 == 0:
            self.refresh_stats()
        self.refresh_events()
        self.stats_tick += 1
        if self.was_running and not pid and "掉线" in self.last_line:
            self.notify_logout()
        self.was_running = bool(pid)
        self.after(1000, self.poll)

    def notify_logout(self):
        msg = "账号掉线了（可能手机上登录了同一个账号），挂机已自动停止。\n登录回来后点\"启动挂机\"继续。"
        try:
            self.tray.notify(msg, "部落冲突挂机")
        except Exception:
            messagebox.showwarning("账号掉线", msg)

    def notify_new_troop(self):
        msg = ("兵种编辑里出现了认不出的兵种（新出的或者换了样子），脚本暂时不用它们，别的照常。\n"
               "截图在 debug\\新兵种.png，找 Claude 补上就能用。")
        try:
            self.tray.notify(msg, "部落冲突挂机")
        except Exception:
            messagebox.showinfo("发现新兵种", msg)

    def start(self):
        if config.running_pid() or time.time() < self.starting_until:
            return
        cfg = self.collect()
        if cfg is None:
            return
        config.save(cfg)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        subprocess.Popen(bot_command(), cwd=HERE, env=env, creationflags=NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.follow.set(True)
        self.starting_until = time.time() + 15
        self.update_header(None)

    def stop(self):
        pid = config.running_pid()
        if not pid:
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        write_log("已停止（控制面板）")

    # ================= 托盘 =================
    def start_tray(self):
        def cmd(name):
            return lambda icon, item: self.tray_cmds.put(name)

        menu = pystray.Menu(
            pystray.MenuItem("显示控制面板", cmd("show"), default=True),  # 双击图标也是这个
            pystray.MenuItem("启动挂机", cmd("start"), enabled=lambda item: not config.running_pid()),
            pystray.MenuItem("停止挂机", cmd("stop"), enabled=lambda item: bool(config.running_pid())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出控制面板", cmd("quit")),
        )
        self.tray = pystray.Icon("coc-bot", tray_image(False), "部落冲突挂机", menu)
        self.tray.run_detached()

    def poll_tray(self):
        while not self.tray_cmds.empty():
            name = self.tray_cmds.get()
            if name == "show":
                self.show_window()
            elif name == "start":
                self.start()
            elif name == "stop":
                self.stop()
            elif name == "quit":
                self.show_window()
                self.on_close()
        self.after(200, self.poll_tray)

    def hide_to_tray(self):
        self.withdraw()
        if not self.tray_tipped:
            self.tray_tipped = True
            try:
                self.tray.notify("控制面板缩到右下角托盘了，挂机照常运行。双击图标打开面板，右键可以停止或退出。",
                                 "部落冲突挂机")
            except Exception:
                pass

    def show_window(self):
        self.deiconify()
        self.wm_state("normal")
        self.lift()
        self.focus_force()

    def update_tray(self, running):
        if self.tray and running != self.tray_running:
            self.tray_running = running
            self.tray.icon = tray_image(running)
            self.tray.title = "部落冲突挂机 - " + ("运行中" if running else "已停止")
            self.tray.update_menu()

    # ================= 关闭 =================
    def dialog(self, title, heading, body, buttons, remember_text=None):
        """统一样式的弹窗。buttons：[(文字, 返回值, 样式)]，样式 primary / danger / plain。
        返回 (点了哪个的返回值, 是否勾了"记住")；直接关窗口返回 (None, False)。"""
        win = ctk.CTkToplevel(self, fg_color=CARD)
        win.title(title)
        self._set_icon(win)
        win.resizable(False, False)
        win.transient(self)
        result = {"v": None}
        remember = tk.BooleanVar(value=False)
        self.label(win, heading, ctk.CTkFont(FONT, 16, "bold")).pack(anchor="w", padx=24, pady=(22, 6))
        self.label(win, body, self.f_body, MUTED, justify="left").pack(anchor="w", padx=24)
        if remember_text:
            ctk.CTkCheckBox(win, text=remember_text, variable=remember, font=self.f_small, fg_color=ACCENT,
                            hover_color=ACCENT_HOVER, text_color=TEXT, checkbox_width=18, checkbox_height=18,
                            border_width=2).pack(anchor="w", padx=24, pady=(14, 0))

        def done(v):
            result["v"] = v
            win.grab_release()
            win.destroy()

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(20, 20))
        for text, value, style in reversed(buttons):
            if style == "primary":
                b = self.button(bar, text, lambda v=value: done(v), primary=True, width=110)
            elif style == "danger":
                b = self.button(bar, text, lambda v=value: done(v), width=110, text_color=RED, border_color=RED,
                                hover_color=RED_SOFT)
            else:
                b = self.button(bar, text, lambda v=value: done(v), width=80)
            b.pack(side="right", padx=(8, 0))
        win.protocol("WM_DELETE_WINDOW", lambda: done(None))
        win.bind("<Escape>", lambda e: done(None))
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.after(120, lambda: (win.grab_set(), win.focus_force()))
        self.wait_window(win)
        return result["v"], remember.get()

    def on_x(self):
        """点了右上角 ✕：按设置退到后台或关闭，没设置就问。"""
        action = config.load()["close_action"]
        if action not in ("tray", "quit"):
            action = self.ask_close()
        if action == "tray":
            self.hide_to_tray() if pystray else self.iconify()
        elif action == "quit":
            self.stop()
            self.quit_app()

    def ask_close(self):
        running = bool(config.running_pid())
        body = (("退到后台：面板缩到右下角托盘，挂机继续运行\n" if pystray else "退到后台：面板最小化，挂机继续运行\n")
                + "关闭脚本：停止挂机" + ("（正在运行）" if running else "") + "，并关掉这个软件")
        v, remember = self.dialog("关闭", "要退到后台，还是关闭脚本？", body,
                                  [("取消", None, "plain"), ("关闭脚本", "quit", "danger"), ("退到后台", "tray", "primary")],
                                  "记住我的选择，下次不再问（可以在\"设置\"里改回来）")
        if v and remember:
            cfg = config.load()
            cfg["close_action"] = v
            config.save(cfg)
            self.close_action.set(CLOSE_ACTIONS[v])
        return v

    def quit_app(self):
        if self.tray:
            self.tray.stop()
        self.destroy()

    def on_close(self):
        """托盘里点"退出控制面板"。"""
        if config.running_pid():
            v, _ = self.dialog("退出控制面板", "挂机还在运行", "停止挂机：停掉挂机再关闭面板\n只关面板：挂机继续在后台跑，"
                               "之后再打开面板可以看日志、停止",
                               [("取消", None, "plain"), ("停止挂机", "stop", "danger"), ("只关面板", "keep", "primary")])
            if v is None:
                return
            if v == "stop":
                self.stop()
        self.quit_app()

    # ================= 设置页 =================
    def build_settings(self, page, cfg):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent", scrollbar_button_color=BORDER)
        scroll.grid(row=0, column=0, sticky="nsew")
        for i in range(2):
            scroll.grid_columnconfigure(i, weight=1, uniform="half")

        # ---- 模拟器 ----
        emu = self.section(scroll, "模拟器", "雷电模拟器 9，分辨率必须是 1600×900")
        emu.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        body = emu.body
        body.grid_columnconfigure(1, weight=1)
        self.adb = tk.StringVar(value=cfg["adb"] or config.find_adb())
        self.label(body, "雷电 adb.exe").grid(row=0, column=0, sticky="w", pady=6)
        ctk.CTkEntry(body, textvariable=self.adb, height=34, font=self.f_small, corner_radius=8,
                     border_color=BORDER).grid(row=0, column=1, sticky="ew", padx=12)
        self.button(body, "浏览…", self.browse, icon="folder", width=90).grid(row=0, column=2)
        self.button(body, "自动查找", self.auto_adb, icon="search", width=104).grid(row=0, column=3, padx=(8, 0))
        self.device = tk.StringVar(value=cfg["device"] or AUTO)
        self.label(body, "操作哪台").grid(row=1, column=0, sticky="w", pady=6)
        self.dev_box = ctk.CTkComboBox(body, variable=self.device, values=[AUTO], height=34, font=self.f_small,
                                       corner_radius=8, border_color=BORDER, button_color=BORDER)
        self.dev_box.grid(row=1, column=1, sticky="ew", padx=12)
        self.button(body, "检测", self.detect, icon="detect", width=90).grid(row=1, column=2)
        self.status = self.label(body, "点\"检测\"查看在线的模拟器和分辨率", self.f_small, MUTED, anchor="w",
                                 justify="left", compound="left")
        self.status.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # ---- 功能开关 ----
        feat = self.section(scroll, "功能", "关掉的功能挂机时就不做")
        feat.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 16))
        self.nums = {}
        self.attack = tk.BooleanVar(value=cfg["attack"])
        self.th_first = tk.BooleanVar(value=cfg["townhall_first"])
        self.research = tk.BooleanVar(value=cfg["research"])
        self.multiplayer_practice = tk.BooleanVar(value=cfg["multiplayer_practice"])
        self.builder_base = tk.BooleanVar(value=cfg["builder_base"])
        self.walls_full = tk.BooleanVar(value=cfg["walls_only_when_full"])
        for r, (var, name, tip) in enumerate((
                (self.attack, "自动进攻抢资源", "有一种资源没满就去打"),
                (self.th_first, "大本营优先", "平时攒金币，够了先升大本营"),
                (self.research, "实验室自动研究", "优先研究最常用的兵种"),
                (self.multiplayer_practice, "满仓多人练兵", "每轮最多 3 场，之后休息 1 小时"),
                (self.builder_base, "建筑大师基地", "每小时最多一次；目前仅支持狂暴野蛮人"),
                (self.walls_full, "城墙只在金币存满时才升", "免得把升大本营的金币花在城墙上"))):
            self.switch_row(feat.body, r, var, name, tip)
        for i, (key, name, tip) in enumerate(BUILDER_NUMBERS):
            self.number_row(feat.body, r + 1 + i, key, name, tip, cfg)

        # ---- 进攻 ----
        atk = self.section(scroll, "进攻", "挑对手的标准")
        atk.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(0, 16))
        for r, (key, name, tip) in enumerate(ATTACK_NUMBERS):
            self.number_row(atk.body, r, key, name, tip, cfg)

        # ---- 升级规则 ----
        up = self.section(scroll, "升级规则", "大本营由\"大本营优先\"控制；帆船、建筑工人小屋永远不升")
        up.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(0, 16))
        self.build_priority(up.body, cfg)

        # ---- 节奏和其他 ----
        other = self.section(scroll, "节奏和其他", "")
        other.grid(row=2, column=1, sticky="nsew", padx=(8, 0), pady=(0, 16))
        for r, (key, name, tip) in enumerate(LOOP_NUMBERS):
            self.number_row(other.body, r, key, name, tip, cfg)
        r = len(LOOP_NUMBERS)
        self.label(other.body, "点面板右上角 ✕ 时").grid(row=r, column=0, sticky="w", pady=(10, 0))
        self.close_action = tk.StringVar(value=CLOSE_ACTIONS.get(cfg["close_action"], CLOSE_ACTIONS["ask"]))
        ctk.CTkOptionMenu(other.body, variable=self.close_action, values=list(CLOSE_ACTIONS.values()), height=32,
                          font=self.f_small, dropdown_font=self.f_small, corner_radius=8, fg_color=GRAY_SOFT,
                          button_color=BORDER, button_hover_color=BORDER, text_color=TEXT).grid(
            row=r + 1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # ---- 微信通知 ----
        push = self.section(scroll, "微信通知", "通过 PushPlus 发送重要进攻、升级和异常提醒")
        push.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        push.body.grid_columnconfigure(1, weight=1)
        self.pushplus_enabled = tk.BooleanVar(value=cfg.get("pushplus_enabled", False))
        self.switch_row(push.body, 0, self.pushplus_enabled, "开启微信通知", "每类提醒设有冷却时间，避免短时间重复打扰")
        self.label(push.body, "PushPlus Token", self.f_body).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.pushplus_token = tk.StringVar(value=cfg.get("pushplus_token", ""))
        ctk.CTkEntry(push.body, textvariable=self.pushplus_token, height=36, font=self.f_body,
                     corner_radius=8, border_color=BORDER, show="•").grid(
            row=1, column=1, sticky="ew", padx=12, pady=(8, 0))
        self.button(push.body, "测试推送", self.test_pushplus, icon="send", width=110).grid(
            row=1, column=2, sticky="e", pady=(8, 0))
        self.pushplus_status = self.label(push.body, "Token 保存在本机 config.json，不会写入日志。",
                                          self.f_small, MUTED, anchor="w")
        self.pushplus_status.grid(row=2, column=1, columnspan=2, sticky="w", padx=12, pady=(6, 0))

        # ---- 底部固定的保存栏 ----
        foot = self.card(page)
        foot.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.button(foot, "恢复默认", self.reset, icon="reset", width=110).pack(side="left", padx=16, pady=12)
        self.saved_tip = self.label(foot, "挂机运行中改了设置，要先停止再启动才生效（点启动时会自动保存）",
                                    self.f_small, MUTED)
        self.saved_tip.pack(side="left", padx=8)
        self.button(foot, "保存设置", self.save, primary=True, icon="save", width=128).pack(side="right", padx=16,
                                                                                         pady=12)

    def section(self, parent, title, desc):
        c = self.card(parent)
        self.label(c, title, self.f_h2).pack(anchor="w", padx=20, pady=(16, 0))
        if desc:
            self.label(c, desc, self.f_small, MUTED).pack(anchor="w", padx=20, pady=(2, 0))
        c.body = ctk.CTkFrame(c, fg_color="transparent")
        c.body.pack(fill="both", expand=True, padx=20, pady=(12, 18))
        c.body.grid_columnconfigure(0, weight=1)
        return c

    def switch_row(self, body, r, var, name, tip):
        cell = ctk.CTkFrame(body, fg_color="transparent")
        cell.grid(row=r, column=0, sticky="ew", pady=6)
        self.label(cell, name, anchor="w").pack(anchor="w")
        self.label(cell, tip, self.f_small, MUTED, anchor="w").pack(anchor="w")
        self.switch(body, text="", variable=var, width=46).grid(row=r, column=1, sticky="e")

    def number_row(self, body, r, key, name, tip, cfg):
        cell = ctk.CTkFrame(body, fg_color="transparent")
        cell.grid(row=r, column=0, sticky="ew", pady=6)
        self.label(cell, name, anchor="w").pack(anchor="w")
        if tip:
            self.label(cell, tip, self.f_small, MUTED, anchor="w", wraplength=240, justify="left").pack(anchor="w")
        v = tk.StringVar(value=str(cfg[key]))
        ctk.CTkEntry(body, textvariable=v, width=100, height=32, font=self.f_body, corner_radius=8, justify="right",
                     border_color=BORDER).grid(row=r, column=1, sticky="e", padx=(12, 0))
        self.nums[key] = v

    def build_priority(self, body, cfg):
        self.rule = tk.StringVar(value=cfg["upgrade_rule"] if cfg["upgrade_rule"] in config.UPGRADE_RULES else "cheap")
        for i, (k, text) in enumerate(config.UPGRADE_RULES.items()):
            ctk.CTkRadioButton(body, text=text, value=k, variable=self.rule, font=self.f_body, fg_color=ACCENT,
                               hover_color=ACCENT_HOVER, text_color=TEXT, radiobutton_width=18,
                               radiobutton_height=18).grid(row=i, column=0, columnspan=2, sticky="w", pady=4)
        self.order = config.priority(cfg)
        self.sel = None
        self.prio = ctk.CTkScrollableFrame(body, height=230, fg_color=GRAY_SOFT, corner_radius=8,
                                           scrollbar_button_color=BORDER)
        self.prio.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        self.prio_rows = []
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=2, column=1, sticky="n", padx=(12, 0), pady=(10, 0))
        self.button(btns, "上移", lambda: self.move(-1), icon="up", width=100).pack(pady=(0, 8))
        self.button(btns, "下移", lambda: self.move(1), icon="down", width=100).pack(pady=(0, 8))
        self.button(btns, "默认顺序", self.reset_order, icon="reset", width=100).pack()
        self.label(body, "均衡：价格一样时按列表顺序，城墙永远排最后\n列表：越靠上越先升，同一档里便宜的先升",
                   self.f_small, MUTED, justify="left").grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.fill_order()

    def fill_order(self, select=None):
        self.sel = select
        while len(self.prio_rows) < len(self.order):
            i = len(self.prio_rows)
            b = ctk.CTkButton(self.prio, text="", anchor="w", height=28, corner_radius=6, font=self.f_small,
                              fg_color="transparent", hover_color=BORDER, text_color=TEXT,
                              command=lambda i=i: self.fill_order(i))
            b.pack(fill="x", pady=1)
            self.prio_rows.append(b)
        for i, (b, k) in enumerate(zip(self.prio_rows, self.order)):
            on = i == select
            b.configure(text=f"{i + 1:>2}.  {config.BUILDINGS[k]}", fg_color=ACCENT if on else "transparent",
                        text_color="#FFFFFF" if on else TEXT)

    def move(self, d):
        if self.sel is None:
            return
        i, j = self.sel, self.sel + d
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self.fill_order(j)

    def reset_order(self):
        self.order = list(config.DEFAULT_PRIORITY)
        self.fill_order()

    def browse(self):
        p = filedialog.askopenfilename(title="选择雷电模拟器目录里的 adb.exe", filetypes=[("adb.exe", "adb.exe")])
        if p:
            self.adb.set(os.path.normpath(p))

    def auto_adb(self):
        p = config.find_adb()
        if p:
            self.adb.set(p)
        else:
            messagebox.showwarning("没找到", "没找到雷电模拟器 9，请点\"浏览…\"手动选它安装目录里的 adb.exe")

    def detect(self):
        adb = self.adb.get().strip()
        if not os.path.isfile(adb):
            self.status.configure(text=" adb.exe 路径不对", text_color=RED, image=ui_icon("error", RED, 16))
            return
        out = run_adb(adb, "devices")
        devs = [l.split("\t")[0] for l in out.splitlines()[1:] if l.endswith("\tdevice")]
        self.dev_box.configure(values=[AUTO, *devs])
        if not devs:
            self.status.configure(text=" 没有在线的模拟器，请先打开雷电模拟器", text_color=RED,
                                  image=ui_icon("error", RED, 16))
            return
        parts, bad = [], False
        for d in devs:
            size = run_adb(adb, "-s", d, "shell", "wm", "size").strip().splitlines()
            size = size[-1].split(":")[-1].strip() if size else "?"
            bad |= size != "1600x900"
            parts.append(f"{d}：{size}" + ("" if size == "1600x900" else "（不对！要改成 1600x900）"))
        tip = "\n分辨率在雷电模拟器右侧 设置→性能设置→分辨率 里改，改完重启模拟器" if bad else ""
        self.status.configure(text=(" " if bad else " 在线：") + "；".join(parts) + tip, text_color=RED if bad else GREEN,
                              image=ui_icon("error" if bad else "done", RED if bad else GREEN, 16))

    def reset(self):
        d = config.DEFAULTS
        self.attack.set(d["attack"])
        self.th_first.set(d["townhall_first"])
        self.research.set(d["research"])
        self.multiplayer_practice.set(d["multiplayer_practice"])
        self.builder_base.set(d["builder_base"])
        for k, v in self.nums.items():
            v.set(str(d[k]))
        self.walls_full.set(d["walls_only_when_full"])
        self.rule.set(d["upgrade_rule"])
        self.close_action.set(CLOSE_ACTIONS[d["close_action"]])
        self.pushplus_enabled.set(d["pushplus_enabled"])
        self.pushplus_token.set(d["pushplus_token"])
        self.reset_order()

    def test_pushplus(self):
        token = self.pushplus_token.get().strip()
        if not token:
            self.pushplus_status.configure(text="先填入 PushPlus Token，再测试。", text_color=RED)
            return
        self.pushplus_status.configure(text="正在发送测试消息…", text_color=ACCENT)

        def worker():
            result = notifier.send(token, "部落冲突挂机 · 测试通知", "PushPlus 微信通知已连接。")
            self.after(0, lambda: self.pushplus_status.configure(
                text=result.message, text_color=GREEN if result.ok else RED))

        threading.Thread(target=worker, name="pushplus-test", daemon=True).start()

    def collect(self):
        cfg = config.load()
        adb = self.adb.get().strip()
        if adb and not os.path.isfile(adb):
            messagebox.showerror("adb 路径不对", f"找不到文件：\n{adb}")
            return None
        cfg["adb"] = adb
        dev = self.device.get().strip()
        cfg["device"] = "" if dev == AUTO else dev
        cfg["attack"] = self.attack.get()
        cfg["townhall_first"] = self.th_first.get()
        cfg["research"] = self.research.get()
        cfg["multiplayer_practice"] = self.multiplayer_practice.get()
        cfg["builder_base"] = self.builder_base.get()
        cfg["priority"] = list(self.order)
        cfg["walls_only_when_full"] = self.walls_full.get()
        cfg["upgrade_rule"] = self.rule.get()
        cfg["close_action"] = next((k for k, v in CLOSE_ACTIONS.items() if v == self.close_action.get()), "ask")
        cfg["pushplus_enabled"] = self.pushplus_enabled.get()
        cfg["pushplus_token"] = self.pushplus_token.get().strip()
        for key, name, _ in NUMBERS:
            try:
                n = int(self.nums[key].get())
                if n < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("填错了", f"\"{name}\"要填不小于 0 的整数")
                return None
            cfg[key] = n
        if cfg["loop_min"] > cfg["loop_max"]:
            messagebox.showerror("填错了", "\"每轮最少等\"不能比\"每轮最多等\"大")
            return None
        if cfg["groups"] < 1:
            messagebox.showerror("填错了", "\"兵分几路\"至少是 1")
            return None
        return cfg

    def save(self):
        cfg = self.collect()
        if cfg is None:
            return
        config.save(cfg)
        msg = " 设置已保存" + ("。挂机正在运行，要先停止再启动才生效" if config.running_pid() else "")
        self.saved_tip.configure(text=msg, text_color=GREEN, image=ui_icon("done", GREEN, 16), compound="left")
        self.after(5000, lambda: self.saved_tip.configure(
            text="挂机运行中改了设置，要先停止再启动才生效（点启动时会自动保存）", text_color=MUTED, image=None))


def already_open():
    """控制面板是不是已经开着（可能缩在托盘里）。用一个命名互斥量判断。"""
    import ctypes
    k32 = ctypes.windll.kernel32
    already_open.handle = k32.CreateMutexW(None, False, "coc-bot-control-panel")  # 保持引用，进程退出自动释放
    return k32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main():
    if already_open():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("部落冲突挂机", "控制面板已经开着了，在屏幕右下角的托盘里（可能要点 ^ 展开），双击图标打开。")
    else:
        try:  # 让任务栏把面板当成独立的程序，用窗口自己的图标
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("coc-bot.panel")
        except Exception:
            pass
        App().mainloop()


if __name__ == "__main__":
    main()
