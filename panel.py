"""挂机控制面板（控制面板.pyw / 打包版 exe 的界面）：启动/停止挂机、看运行日志、改设置。挂机在后台运行，没有黑窗口，面板可以最小化。"""
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config

try:  # 托盘图标（pip install pystray pillow）；没装就退回普通的关闭
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None

HERE = config.BASE
LOG_FILE = HERE / "bot.log"
AUTO = "自动（用在线的那台）"
NO_WINDOW = config.NO_WINDOW
MAX_LINES = 3000          # 日志框最多保留多少行
TAIL_BYTES = 30000        # 打开面板时先显示 bot.log 最后这么多内容

CLOSE_ACTIONS = {"ask": "每次都问", "tray": "退到后台（挂机继续）", "quit": "关闭脚本（停止挂机并退出）"}

NUMBERS = [  # (键, 名称, 说明)
    ("loop_min", "每轮最少等（秒）", ""),
    ("loop_max", "每轮最多等（秒）", ""),
    ("max_not_home", "几轮回不到村庄就重启游戏", ""),
    ("min_gold", "对手金币至少多少才打", "金币存满了就不看这一项"),
    ("min_elixir", "对手圣水至少多少才打", "圣水存满了就不看这一项；两种都不够就换下一个"),
    ("max_skips", "最多换几个对手", "每换一次只花一点金币，换满了就将就打"),
    ("groups", "兵分几路", ""),
]


def run_adb(adb, *args):
    try:
        return subprocess.run([adb, *args], capture_output=True, text=True, timeout=15,
                              creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def bot_command():
    """启动挂机的命令：打包版用 exe 自己加 --bot；源码版用 python.exe（不是 pythonw.exe）跑 bot.py。"""
    if config.FROZEN:
        return [sys.executable, "--bot"]
    p = Path(sys.executable).with_name("python.exe")
    return [str(p) if p.is_file() else sys.executable, "-u", "bot.py"]


def tray_image(running):
    """托盘小图标：运行中是绿点，停止是灰点。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(235, 170, 40), outline=(90, 60, 10), width=4)  # 金币底
    d.ellipse((20, 20, 44, 44), fill=(40, 190, 70) if running else (140, 140, 140))
    return img


def write_log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('[%m-%d %H:%M:%S]')} {msg}\n")
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("部落冲突挂机 - 控制面板")
        self.geometry("940x680")
        self.minsize(680, 460)
        self.protocol("WM_DELETE_WINDOW", self.on_x)
        self.log_pos = 0
        self.last_line = ""
        self.was_running = None
        self.tray = None
        self.tray_running = None
        self.tray_tipped = False
        self.tray_cmds = queue.Queue()  # 托盘菜单在别的线程里，命令通过队列交给界面线程执行

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        run_tab, set_tab = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(run_tab, text="  运行  ")
        tabs.add(set_tab, text="  设置  ")
        self.build_run(run_tab)
        self.build_settings(set_tab)

        self.load_log_tail()
        self.poll()
        self.after(300, self.detect)
        if pystray:
            self.start_tray()
            self.poll_tray()

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
        self.state_normal()

    def state_normal(self):
        self.wm_state("normal")
        self.lift()
        self.focus_force()

    def update_tray(self, running):
        if self.tray and running != self.tray_running:
            self.tray_running = running
            self.tray.icon = tray_image(running)
            self.tray.title = "部落冲突挂机 - " + ("运行中" if running else "已停止")
            self.tray.update_menu()

    # ================= 运行页 =================
    def build_run(self, tab):
        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=6, pady=6)
        self.start_btn = ttk.Button(bar, text="▶ 启动挂机", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ 停止挂机", command=self.stop)
        self.stop_btn.pack(side="left", padx=6)
        self.state = ttk.Label(bar, text="", font=("Microsoft YaHei UI", 10, "bold"))
        self.state.pack(side="left", padx=12)
        ttk.Button(bar, text="清空显示", command=lambda: self.text.delete("1.0", "end")).pack(side="right")
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="自动滚到最新", variable=self.follow).pack(side="right", padx=8)

        self.stats = ttk.Label(tab, text="", foreground="#2a6fb0", wraplength=900, justify="left")
        self.stats.pack(fill="x", padx=8, pady=(0, 4))
        self.stats_tick = 0

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.text = tk.Text(frame, wrap="none", font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
                            insertbackground="#d4d4d4")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.text.tag_configure("warn", foreground="#f0a050")
        self.text.tag_configure("err", foreground="#f06060")
        self.text.tag_configure("good", foreground="#7fd07f")
        self.text.bind("<Key>", lambda e: None if e.state & 4 else "break")  # 只读，但能 Ctrl+C 复制

        ttk.Label(tab, text="挂机在后台运行。点右上角 ✕ 会问是退到后台（挂机不停）还是关闭脚本。日志同时保存在 bot.log",
                  foreground="gray").pack(anchor="w", padx=6, pady=(0, 4))

    def append(self, chunk):
        if chunk.strip():
            self.last_line = chunk.strip().splitlines()[-1]
        at_bottom = self.text.yview()[1] > 0.999
        for line in chunk.splitlines(keepends=True):
            tag = ()
            if "出错" in line or "Traceback" in line or "Error" in line or "连不上" in line or "找不到" in line:
                tag = ("err",)
            elif "宝石" in line or "掉线" in line or "分辨率" in line or "重启" in line:
                tag = ("warn",)
            elif "开打" in line or ("升级" in line and "没" not in line) or "建造" in line or "启动" in line:
                tag = ("good",)
            self.text.insert("end", line, tag)
        extra = int(self.text.index("end-1c").split(".")[0]) - MAX_LINES
        if extra > 0:
            self.text.delete("1.0", f"{extra + 1}.0")
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

    def poll(self):
        try:
            size = LOG_FILE.stat().st_size
            if size < self.log_pos:  # 日志被删了或清空了
                self.log_pos = 0
            if size > self.log_pos:
                text, self.log_pos = self.read_log(self.log_pos)
                self.append(text)
        except OSError:
            pass
        pid = config.running_pid()
        if pid:
            self.state.config(text=f"● 运行中（进程 {pid}）", foreground="#2a9d2a")
            self.start_btn.state(["disabled"])
            self.stop_btn.state(["!disabled"])
        else:
            self.state.config(text="● 已停止", foreground="#c03030")
            self.start_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
        self.update_tray(bool(pid))
        if self.stats_tick % 10 == 0:
            self.refresh_stats()
        self.stats_tick += 1
        if self.was_running and not pid and "掉线" in self.last_line:
            self.notify_logout()
        self.was_running = bool(pid)
        self.after(1000, self.poll)

    def refresh_stats(self):
        """运行页顶上的今日战果（读 battles.csv）。"""
        try:
            import stats
            d = stats.summary()
        except Exception:
            return
        if not d["场数"] and not d["各配兵"]:
            self.stats.config(text="今日战果：还没打过仗")
            return
        text = (f"今日战果：打了 {d['场数']} 场，抢到 金币 {d['金币']:,} / 圣水 {d['圣水']:,}，"
                f"平均抢到对手的 {d['平均比例']:.0%}")
        if d["各配兵"]:
            text += "\n配兵成绩（最近 20 场平均抢到比例）：" + "，".join(
                f"{k} {v[0]:.0%}（{v[1]} 场）" for k, v in sorted(d["各配兵"].items(), key=lambda t: -t[1][0]))
        self.stats.config(text=text)

    def notify_logout(self):
        msg = "账号掉线了（可能手机上登录了同一个账号），挂机已自动停止。\n登录回来后点\"启动挂机\"继续。"
        try:
            self.tray.notify(msg, "部落冲突挂机")
        except Exception:
            messagebox.showwarning("账号掉线", msg)

    def start(self):
        if config.running_pid():
            return
        cfg = self.collect()
        if cfg is None:
            return
        config.save(cfg)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        subprocess.Popen(bot_command(), cwd=HERE, env=env, creationflags=NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.follow.set(True)
        self.state.config(text="● 启动中…", foreground="#c08000")

    def stop(self):
        pid = config.running_pid()
        if not pid:
            return
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        write_log("已停止（控制面板）")

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
        """弹窗问：退到后台 / 关闭脚本 / 取消。返回 "tray" / "quit" / None。"""
        win = tk.Toplevel(self)
        win.title("关闭")
        win.resizable(False, False)
        win.transient(self)
        result = {"v": None}
        remember = tk.BooleanVar(value=False)
        running = bool(config.running_pid())
        ttk.Label(win, text="要退到后台，还是关闭脚本？", font=("Microsoft YaHei UI", 11, "bold")).pack(
            anchor="w", padx=16, pady=(14, 4))
        ttk.Label(win, text=("退到后台：面板缩到右下角托盘，挂机继续运行\n" if pystray else
                             "退到后台：面板最小化，挂机继续运行\n")
                  + "关闭脚本：停止挂机" + ("（正在运行）" if running else "") + "，并关掉这个软件",
                  foreground="gray", justify="left").pack(anchor="w", padx=16)
        ttk.Checkbutton(win, text="记住我的选择，下次不再问（可以在\"设置\"里改回来）", variable=remember).pack(
            anchor="w", padx=16, pady=(10, 0))

        def done(v):
            result["v"] = v
            if v and remember.get():
                cfg = config.load()
                cfg["close_action"] = v
                config.save(cfg)
                self.close_action.set(CLOSE_ACTIONS[v])
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=16, pady=14)
        ttk.Button(bar, text="退到后台", command=lambda: done("tray")).pack(side="left")
        ttk.Button(bar, text="关闭脚本", command=lambda: done("quit")).pack(side="left", padx=8)
        ttk.Button(bar, text="取消", command=lambda: done(None)).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: done(None))
        win.bind("<Escape>", lambda e: done(None))
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        win.focus_force()
        self.wait_window(win)
        return result["v"]

    def quit_app(self):
        if self.tray:
            self.tray.stop()
        self.destroy()

    def on_close(self):
        if config.running_pid():
            ans = messagebox.askyesnocancel(
                "挂机还在运行", "挂机还在运行。\n\n是：停止挂机并关闭面板\n否：挂机继续在后台运行，只关面板"
                                "（之后再打开面板可以看日志、停止）\n取消：不关")
            if ans is None:
                return
            if ans:
                self.stop()
        self.quit_app()

    # ================= 设置页 =================
    def build_settings(self, tab):
        cfg = config.load()
        pad = {"padx": 8, "pady": 4}

        box = ttk.LabelFrame(tab, text="模拟器")
        box.pack(fill="x", padx=6, pady=(8, 4))
        ttk.Label(box, text="雷电 adb.exe").grid(row=0, column=0, sticky="w", **pad)
        self.adb = tk.StringVar(value=cfg["adb"] or config.find_adb())
        ttk.Entry(box, textvariable=self.adb, width=50).grid(row=0, column=1, **pad)
        ttk.Button(box, text="浏览…", command=self.browse).grid(row=0, column=2, **pad)
        ttk.Button(box, text="自动查找", command=self.auto_adb).grid(row=0, column=3, **pad)

        ttk.Label(box, text="操作哪台").grid(row=1, column=0, sticky="w", **pad)
        self.device = tk.StringVar(value=cfg["device"] or AUTO)
        self.dev_box = ttk.Combobox(box, textvariable=self.device, width=48)
        self.dev_box.grid(row=1, column=1, **pad)
        ttk.Button(box, text="检测", command=self.detect).grid(row=1, column=2, **pad)
        self.status = ttk.Label(box, text="点\"检测\"查看在线的模拟器和分辨率（必须是 1600x900）",
                                foreground="gray", wraplength=640)
        self.status.grid(row=2, column=0, columnspan=4, sticky="w", **pad)

        row = ttk.Frame(tab)
        row.pack(fill="both", expand=True, padx=6, pady=4)
        box2 = ttk.LabelFrame(row, text="挂机选项")
        box2.pack(side="left", fill="both", expand=True)
        self.build_priority(row, cfg)
        self.attack = tk.BooleanVar(value=cfg["attack"])
        self.th_first = tk.BooleanVar(value=cfg["townhall_first"])
        self.research = tk.BooleanVar(value=cfg["research"])
        ttk.Checkbutton(box2, text="自动进攻抢资源", variable=self.attack).grid(row=0, column=0, sticky="w", **pad)
        ttk.Checkbutton(box2, text="大本营优先（平时攒金币，够了先升大本营）", variable=self.th_first).grid(
            row=0, column=1, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(box2, text="实验室自动研究（先研究最便宜的，也就是等级低的兵种）", variable=self.research).grid(
            row=1, column=0, columnspan=3, sticky="w", **pad)
        self.nums = {}
        for r, (key, name, tip) in enumerate(NUMBERS, start=2):
            ttk.Label(box2, text=name).grid(row=r, column=0, sticky="w", **pad)
            v = tk.StringVar(value=str(cfg[key]))
            ttk.Entry(box2, textvariable=v, width=10).grid(row=r, column=1, sticky="w", **pad)
            ttk.Label(box2, text=tip, foreground="gray").grid(row=r, column=2, sticky="w", **pad)
            self.nums[key] = v
        r = len(NUMBERS) + 2
        ttk.Label(box2, text="点面板右上角 ✕ 时").grid(row=r, column=0, sticky="w", **pad)
        self.close_action = tk.StringVar(value=CLOSE_ACTIONS.get(cfg["close_action"], CLOSE_ACTIONS["ask"]))
        ttk.Combobox(box2, textvariable=self.close_action, values=list(CLOSE_ACTIONS.values()), state="readonly",
                     width=24).grid(row=r, column=1, columnspan=2, sticky="w", **pad)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=6, pady=8)
        ttk.Button(bar, text="恢复默认", command=self.reset).pack(side="left")
        ttk.Button(bar, text="保存", command=self.save).pack(side="right")
        ttk.Label(tab, text="挂机运行中改了设置，要先\"停止\"再\"启动\"才生效（点\"启动挂机\"时会自动保存）",
                  foreground="gray").pack(anchor="w", padx=6)

    def build_priority(self, parent, cfg):
        box = ttk.LabelFrame(parent, text="升级规则")
        box.pack(side="left", fill="y", padx=(8, 0))
        self.rule = tk.StringVar(value=cfg["upgrade_rule"] if cfg["upgrade_rule"] in config.UPGRADE_RULES else "cheap")
        for i, (k, text) in enumerate(config.UPGRADE_RULES.items()):
            ttk.Radiobutton(box, text=text, value=k, variable=self.rule).grid(
                row=i, column=0, columnspan=2, sticky="w", padx=8, pady=(6 if i == 0 else 0, 0))
        self.order = config.priority(cfg)
        self.prio = tk.Listbox(box, height=12, width=24, activestyle="none", exportselection=False)
        self.prio.grid(row=2, column=0, rowspan=4, padx=(8, 4), pady=6, sticky="ns")
        ttk.Button(box, text="↑ 上移", width=8, command=lambda: self.move(-1)).grid(row=2, column=1, padx=4, pady=(6, 2))
        ttk.Button(box, text="↓ 下移", width=8, command=lambda: self.move(1)).grid(row=3, column=1, padx=4, pady=2)
        ttk.Button(box, text="默认顺序", width=8, command=self.reset_order).grid(row=4, column=1, padx=4, pady=2)
        self.walls_full = tk.BooleanVar(value=cfg["walls_only_when_full"])
        ttk.Checkbutton(box, text="城墙只在金币存满时才升", variable=self.walls_full).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=8)
        ttk.Label(box, text="均衡：价格一样时按列表顺序；城墙永远排最后\n列表：越靠上越先升，同一档里便宜的先升\n"
                            "大本营由左边\"大本营优先\"控制，\n帆船、建筑工人小屋永远不升", foreground="gray",
                  justify="left").grid(row=7, column=0, columnspan=2, sticky="w", padx=8, pady=(2, 6))
        self.fill_order()

    def fill_order(self, select=None):
        self.prio.delete(0, "end")
        for i, k in enumerate(self.order, 1):
            self.prio.insert("end", f"{i:>2}. {config.BUILDINGS[k]}")
        if select is not None:
            self.prio.selection_set(select)
            self.prio.see(select)

    def move(self, d):
        sel = self.prio.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
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
            self.status.config(text="adb.exe 路径不对", foreground="red")
            return
        out = run_adb(adb, "devices")
        devs = [l.split("\t")[0] for l in out.splitlines()[1:] if l.endswith("\tdevice")]
        self.dev_box["values"] = [AUTO, *devs]
        if not devs:
            self.status.config(text="没有在线的模拟器，请先打开雷电模拟器", foreground="red")
            return
        parts, bad = [], False
        for d in devs:
            size = run_adb(adb, "-s", d, "shell", "wm", "size").strip().splitlines()
            size = size[-1].split(":")[-1].strip() if size else "?"
            bad |= size != "1600x900"
            parts.append(f"{d}：{size}" + ("" if size == "1600x900" else "（不对！要改成 1600x900）"))
        tip = "\n分辨率在雷电模拟器右侧 设置→性能设置→分辨率 里改，改完重启模拟器" if bad else ""
        self.status.config(text="在线：" + "；".join(parts) + tip, foreground="red" if bad else "green")

    def reset(self):
        d = config.DEFAULTS
        self.attack.set(d["attack"])
        self.th_first.set(d["townhall_first"])
        self.research.set(d["research"])
        for k, v in self.nums.items():
            v.set(str(d[k]))
        self.walls_full.set(d["walls_only_when_full"])
        self.rule.set(d["upgrade_rule"])
        self.close_action.set(CLOSE_ACTIONS[d["close_action"]])
        self.reset_order()

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
        cfg["priority"] = list(self.order)
        cfg["walls_only_when_full"] = self.walls_full.get()
        cfg["upgrade_rule"] = self.rule.get()
        cfg["close_action"] = next((k for k, v in CLOSE_ACTIONS.items() if v == self.close_action.get()), "ask")
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
        msg = "设置已保存。"
        if config.running_pid():
            msg += "\n挂机正在运行，要先\"停止\"再\"启动\"才生效。"
        messagebox.showinfo("已保存", msg)


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
        App().mainloop()


if __name__ == "__main__":
    main()
