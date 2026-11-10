"""PushPlus WeChat notifications for important bot events."""
import json
import threading
import time
from dataclasses import dataclass
from urllib import error, request

from . import config

API_URL = "https://www.pushplus.plus/send"
_last_sent = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message: str


def send(token, title, content, timeout=10):
    """Send one PushPlus message; return a safe, user-facing result."""
    token = (token or "").strip()
    if not token:
        return SendResult(False, "请先填写 PushPlus Token")
    payload = json.dumps({
        "token": token,
        "title": title[:64],
        "content": content[:4000],
        "channel": "wechat",
        "template": "markdown",
    }, ensure_ascii=False).encode("utf-8")
    req = request.Request(API_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("code") == 200:
            return SendResult(True, "推送成功")
        return SendResult(False, "PushPlus 返回失败，请检查 Token 和账号状态")
    except (error.URLError, TimeoutError, OSError, ValueError):
        return SendResult(False, "网络异常或 PushPlus 暂不可用")


def event_for(message):
    """Map a bot log line to a concise notification, or None for routine logs."""
    categories = (
        (("本场抢到", "多人练兵结果", "建筑大师基地：进攻完成"), "进攻结果"),
        (("已开始升级", "已开始研究", "已建造"), "升级/研究"),
        (("账号已掉线", "出错", "Traceback", "重启游戏"), "挂机异常"),
        (("发现新兵种",), "需要关注"),
        (("脚本启动",), "挂机已启动"),
    )
    for keys, title in categories:
        if any(key in message for key in keys):
            return title, message[:240]
    return None


def notify(title, content, cfg=None, cooldown=900):
    """Send in a daemon thread, with per-title cooldown to avoid message storms."""
    cfg = cfg or config.load()
    token = cfg.get("pushplus_token", "")
    if not cfg.get("pushplus_enabled") or not token:
        return False
    now = time.monotonic()
    with _lock:
        if now - _last_sent.get(title, 0) < cooldown:
            return False
        _last_sent[title] = now

    def worker():
        result = send(token, title, content)
        if not result.ok:
            # Never include the token or server-provided response in logs.
            try:
                with open(config.BASE / "bot.log", "a", encoding="utf-8") as f:
                    f.write(f"[PushPlus] {result.message}\n")
            except OSError:
                pass

    threading.Thread(target=worker, name="pushplus-notify", daemon=True).start()
    return True
