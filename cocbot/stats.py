"""战果统计 + 自动配兵。

每场战斗记一行到 battles.csv：用的哪套配兵、对手有多少战利品、打完还剩多少、抢到多少、打了多久。
自动配兵：几套方案轮流试，按"抢到对手战利品的比例"打分（只看抢到多少钱的话，太受对手贫富影响）。
每套都试满 MIN_TRIES 场后，大部分时候用成绩最好的那套，偶尔试试别的，对手变了也能跟着调整。
"""
import csv
import random
import time

from .config import BASE

BATTLES = BASE / "battles.csv"
FIELDS = ["时间", "配兵", "对手金币", "对手圣水", "剩余金币", "剩余圣水", "抢到金币", "抢到圣水", "抢到比例", "秒数", "换了几个"]

# 配兵方案：(兵种, 占总人口的比例)，剩下的人口补野蛮人。兵种代号见 attack.ARMY_CARDS
PLANS = {
    "均衡": [("giant", 0.30), ("breaker", 0.09), ("archer", 0.30), ("goblin", 0.16)],
    "哥布林多": [("giant", 0.22), ("breaker", 0.07), ("archer", 0.25), ("goblin", 0.35)],
    "巨人多": [("giant", 0.40), ("breaker", 0.09), ("archer", 0.30), ("goblin", 0.10)],
    "弓箭手多": [("giant", 0.22), ("breaker", 0.07), ("archer", 0.45), ("goblin", 0.16)],
    "亡灵": [("giant", 0.22), ("breaker", 0.07), ("archer", 0.25), ("goblin", 0.18), ("minion", 0.18)],  # 165 人口时亡灵 15 个
    "气球": [("giant", 0.22), ("breaker", 0.07), ("balloon", 0.27), ("archer", 0.25), ("goblin", 0.14)],  # 185 人口时气球兵 10 个
    # 新兵种（人口大，按比例算出来的个数会四舍五入）。115 人口时：
    "戈仑图腾": [("witchgolem", 0.72), ("totem", 0.25)],                       # 女巫戈仑2、图腾投手1
    "骷髅巨人": [("skelgiant", 0.35), ("totem", 0.25), ("archer", 0.20)],      # 骷髅巨人2、图腾投手1、弓箭手23
    "戈仑骷髅": [("witchgolem", 0.36), ("skelgiant", 0.35), ("totem", 0.25)],  # 女巫戈仑1、骷髅巨人2、图腾投手1
}
MIN_TRIES = 5      # 每套至少试几场
EXPLORE = 0.2      # 试满以后，还有多大概率换一套试试
RECENT = 20        # 每套只看最近多少场（对手会随奖杯变化）


def _rows():
    try:
        with open(BATTLES, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def record(plan, offered, left, seconds, skips, got=(None, None)):
    """记一场。offered：对手开打时的战利品；left：最后读到的剩余；got：结算页上"您得到了"的数字。
    都是 (金币, 圣水)。抢到多少优先用结算页的数字，读不出才用 对手的 - 剩余的。"""
    og, oe = (v or 0 for v in offered)
    lg, le = (v if v is not None else o for v, o in zip(left, (og, oe)))
    tg = got[0] if got[0] is not None else max(0, og - lg)
    te = got[1] if got[1] is not None else max(0, oe - le)
    lg, le = max(0, og - tg), max(0, oe - te)
    ratio = min(1.0, (tg + te) / (og + oe)) if og + oe else 0
    new = not BATTLES.exists()
    try:
        with open(BATTLES, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(FIELDS)
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), plan, og, oe, lg, le, tg, te, f"{ratio:.3f}",
                        int(seconds), skips])
    except OSError:
        pass
    return tg, te, ratio


def plan_scores():
    """每套配兵最近 RECENT 场的 (平均抢到比例, 场数)。"""
    by = {k: [] for k in PLANS}
    for r in _rows():
        if r.get("配兵") in by:
            try:
                by[r["配兵"]].append(float(r["抢到比例"]))
            except (TypeError, ValueError):
                pass
    return {k: (sum(v[-RECENT:]) / len(v[-RECENT:]) if v else 0.0, len(v)) for k, v in by.items()}


def choose_plan(available=None):
    """挑这一场用哪套配兵，返回 (方案名, 原因)。available：现在有哪些兵种（None＝不知道），
    方案里的兵种不全的不选；一套都配不出来就不挑了，照常在所有方案里选。"""
    scores = plan_scores()
    if available is not None:
        ok = {k: v for k, v in scores.items() if all(t in available for t, _ in PLANS[k])}
        scores = ok or scores
    untried = [k for k, (_, n) in scores.items() if n < MIN_TRIES]
    if untried:
        k = min(untried, key=lambda k: scores[k][1])
        return k, f"还在试，这套打了 {scores[k][1]} 场"
    best = max(scores, key=lambda k: scores[k][0])
    if random.random() < EXPLORE:
        k = random.choice([k for k in scores if k != best] or [best])
        return k, f"偶尔换着试（目前最好的是{best}）"
    return best, f"成绩最好，平均抢到 {scores[best][0]:.0%}"


def summary(day=None):
    """某天（默认今天）的战果：dict(场数, 金币, 圣水, 平均比例, 最好配兵)。"""
    day = day or time.strftime("%Y-%m-%d")
    rows = [r for r in _rows() if r.get("时间", "").startswith(day)]
    gold = sum(int(r["抢到金币"] or 0) for r in rows)
    elixir = sum(int(r["抢到圣水"] or 0) for r in rows)
    avg = sum(float(r["抢到比例"] or 0) for r in rows) / len(rows) if rows else 0
    scores = {k: v for k, v in plan_scores().items() if v[1]}
    best = max(scores, key=lambda k: scores[k][0]) if scores else ""
    return {"场数": len(rows), "金币": gold, "圣水": elixir, "平均比例": avg, "最好配兵": best,
            "各配兵": scores}
