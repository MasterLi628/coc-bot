"""用 sendevent 模拟双指捏合缩小视角（雷电 /dev/input/event2 多点触控）。
用法：python pinch.py [次数]"""
import subprocess, sys, tempfile, os
from adbtool import ADB, device

DEV = "/dev/input/event2"


def _script(cx=800, cy=450, start=400, end=70, steps=12):
    L = []
    e = lambda t, c, v: L.append(f"sendevent {DEV} {t} {c} {v}")
    syn = lambda: e(0, 0, 0)
    for i in range(steps + 1):
        d = int(start + (end - start) * i / steps)
        for slot, sign in ((0, -1), (1, 1)):
            e(3, 47, slot)
            if i == 0:
                e(3, 57, 100 + slot)
            e(3, 53, cx + sign * d)
            e(3, 54, cy + sign * int(d * 0.6))
        if i == 0:
            e(1, 330, 1)
        syn()
    for slot in (0, 1):
        e(3, 47, slot)
        e(3, 57, 4294967295)
    e(1, 330, 0)
    syn()
    return "\n".join(L) + "\n"


_pushed = False


def pinch_out(times=1):
    """双指捏合，把村庄画面缩小 times 次（缩到最小后再捏也没变化）。"""
    global _pushed
    from config import NO_WINDOW
    dev = device()
    if not dev:
        return
    if not _pushed:
        path = os.path.join(tempfile.gettempdir(), "pinch.sh")
        with open(path, "w", newline="\n") as f:
            f.write(_script())
        subprocess.run([ADB, "-s", dev, "push", path, "/data/local/tmp/pinch.sh"], capture_output=True,
                       creationflags=NO_WINDOW)
        _pushed = True
    for _ in range(times):
        subprocess.run([ADB, "-s", dev, "shell", "sh", "/data/local/tmp/pinch.sh"], capture_output=True,
                       creationflags=NO_WINDOW)


if __name__ == "__main__":
    pinch_out(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
