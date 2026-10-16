"""ADB 小工具：截图、点击。命令行用法：
    python adbtool.py shot out.png
    python adbtool.py tap x y [out.png]   # 点击后等 1.5 秒截图
"""
import subprocess
import sys
import time

import cv2
import numpy as np

from config import CFG, NO_WINDOW, adb_path

ADB = adb_path(CFG)             # 雷电的 adb.exe，在"挂机控制面板.bat"里改，空着就自动查找
PREFERRED = CFG["device"]       # 同时开多个模拟器时优先这台；空着或找不到就用在线的那台


def online_devices():
    if not ADB:
        return []
    try:
        out = subprocess.run([ADB, "devices"], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split("	")[0] for line in out.splitlines()[1:] if line.endswith("	device")]


def device():
    """当前要操作的模拟器编号；没有在线设备时返回 None。"""
    devs = online_devices()
    if PREFERRED in devs:
        return PREFERRED
    return devs[0] if devs else None


def adb(*args, binary=False):
    out = subprocess.run([ADB, "-s", device() or PREFERRED or "emulator-5554", *args], capture_output=True, check=True, timeout=30,
                         creationflags=NO_WINDOW).stdout
    return out if binary else out.decode("utf-8", "ignore")


def screenshot():
    """返回 BGR 图像（numpy）。"""
    png = adb("exec-out", "screencap", "-p", binary=True)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("截图失败（模拟器可能没开或者卡住了）")
    return img


def screen_size():
    """模拟器分辨率，如 (1600, 900)；读不到返回 None。"""
    try:
        out = adb("shell", "wm", "size")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(out.splitlines()):  # 有 Override size 时以它为准（在最后一行）
        if "size:" in line:
            w, h = line.split(":")[1].strip().split("x")
            return int(w), int(h)
    return None


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def swipe(x1, y1, x2, y2, ms=300):
    adb("shell", "input", "swipe", *map(str, (int(x1), int(y1), int(x2), int(y2), ms)))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "shot":
        cv2.imwrite(sys.argv[2], screenshot())
    elif cmd == "tap":
        tap(sys.argv[2], sys.argv[3])
        if len(sys.argv) > 4:
            time.sleep(1.5)
            cv2.imwrite(sys.argv[4], screenshot())
