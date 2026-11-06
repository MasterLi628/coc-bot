"""ADB 小工具：截图、点击。命令行用法：
    python -m cocbot.adbtool shot out.png
    python -m cocbot.adbtool tap x y [out.png]   # 点击后等 1.5 秒截图
"""
import atexit
import subprocess
import sys
import time

import cv2
import numpy as np

from .config import CFG, NO_WINDOW, adb_path

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


_dev = (None, 0.0)  # (设备编号, 查到的时间)：每条 adb 命令都去查一遍设备太慢，30 秒内沿用


def _cached_device():
    global _dev
    if _dev[0] is None or time.time() - _dev[1] > 30:
        _dev = (device(), time.time())
    return _dev[0]


def adb(*args, binary=False):
    global _dev
    try:
        out = subprocess.run([ADB, "-s", _cached_device() or PREFERRED or "emulator-5554", *args], capture_output=True,
                             check=True, timeout=30, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        _dev = (None, 0.0)  # 模拟器可能重启换了编号，下次重新查
        raise
    return out if binary else out.decode("utf-8", "ignore")


_stream = None
_last_input = 0.0
_capture_counts = {'video': 0, 'screenshot': 0}


def capture_counts():
    return dict(_capture_counts)


def _stream_frame():
    global _stream
    if not CFG.get('video_stream', False):
        return None
    device_id = _cached_device()
    if not ADB or not device_id:
        return None
    if _stream is None:
        from .video_stream import VideoStream
        _stream = VideoStream(ADB, device_id)
        _stream.start()
        atexit.register(_stream.close)
    if _stream.device != device_id:
        _stream.close()
        _stream = None
        return None
    # Decode time is not capture time. Remain conservative after each input.
    if time.monotonic() - _last_input < 0.5:
        return None
    return _stream.latest(after=_last_input + 0.5)


def screenshot():
    frame = _stream_frame()
    _capture_counts['video' if frame is not None else 'screenshot'] += 1
    return frame if frame is not None else _screencap()


def _screencap():
    """返回 BGR 图像（numpy）。
    不加 -p：模拟器直接传原始像素（文件头 + RGBA），省掉在模拟器里压 PNG 的时间，比 PNG 快一倍多，画面完全一样。"""
    raw = adb("exec-out", "screencap", binary=True)
    if len(raw) >= 12:
        w, h = (int(v) for v in np.frombuffer(raw[:8], np.uint32))
        body = len(raw) - w * h * 4  # 文件头 12 或 16 字节（新版 Android 多一个色彩空间字段）
        if 0 < w <= 8000 and 0 < h <= 8000 and body in (12, 16):
            return cv2.cvtColor(np.frombuffer(raw, np.uint8, offset=body).reshape(h, w, 4), cv2.COLOR_RGBA2BGR)
    raise RuntimeError("截图失败（模拟器可能没开或者卡住了）")


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
    global _last_input
    adb("shell", "input", "tap", str(int(x)), str(int(y)))
    _last_input = time.monotonic()


def swipe(x1, y1, x2, y2, ms=300):
    global _last_input
    adb("shell", "input", "swipe", *map(str, (int(x1), int(y1), int(x2), int(y2), ms)))
    _last_input = time.monotonic()


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "shot":
        cv2.imwrite(sys.argv[2], screenshot())
    elif cmd == "tap":
        tap(sys.argv[2], sys.argv[3])
        if len(sys.argv) > 4:
            time.sleep(1.5)
            cv2.imwrite(sys.argv[4], screenshot())
