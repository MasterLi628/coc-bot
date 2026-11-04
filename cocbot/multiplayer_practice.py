"""多人练兵：独立战绩和持久化场次限制。"""
import csv
import json
import time
from pathlib import Path

import cv2

from .config import BASE
from .scout import read_green
from .stats import PLANS

STATE = BASE / 'multiplayer_practice_state.json'
FILE = BASE / 'multiplayer_practice.csv'
ROUND_SIZE = 3
REST_SECONDS = 3600
BAD_REST_SECONDS = 4 * 3600


def _state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'count': 0, 'bad': 0, 'rest_until': 0}


def _save(state):
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state), encoding='utf-8')
    tmp.replace(STATE)


def rest_remaining(now=None):
    now = time.time() if now is None else now
    return max(0, _state().get('rest_until', 0) - now)


def resting(now=None):
    return rest_remaining(now) > 0


def reserve(now=None):
    """开始前记录一次尝试；异常退出或重启也不会绕过上限。"""
    now = time.time() if now is None else now
    state = _state()
    state['count'] = state.get('count', 0) + 1
    if state['count'] >= ROUND_SIZE:
        state.update(count=0, rest_until=now + REST_SECONDS)
    _save(state)


def record(plan, stars, percent, now=None):
    now = time.time() if now is None else now
    new = not FILE.exists()
    with FILE.open('a', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['time', 'plan', 'stars', 'percent'])
        w.writerow([int(now), plan, '' if stars is None else stars, '' if percent is None else percent])
    state = _state()
    state['bad'] = state.get('bad', 0) + 1 if percent is not None and percent < 30 else 0
    if state['bad'] >= 3:
        state.update(count=0, bad=0, rest_until=now + BAD_REST_SECONDS)
    _save(state)


def scores():
    by = {name: [] for name in PLANS}
    try:
        with FILE.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                try:
                    if row['plan'] in by and row['stars'] != '' and row['percent'] != '':
                        by[row['plan']].append(int(row['stars']) * 100 + int(row['percent']))
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return {k: (sum(v[-20:]) / len(v[-20:]) if v else 0, len(v)) for k, v in by.items()}


def choose_plan(available=None):
    options = scores()
    if available is not None:
        filtered = {k: v for k, v in options.items() if all(t in available for t, _ in PLANS[k])}
        options = filtered or options
    untried = [k for k, (_, n) in options.items() if n < 3]
    if untried:
        name = min(untried, key=lambda k: options[k][1])
    elif len(options) > 1 and sum(n for _, n in options.values()) % 3 == 0:
        name = min(options, key=lambda k: (options[k][1], -options[k][0]))
    else:
        name = max(options, key=lambda k: options[k][0])
    return name


def read_result(img):
    percent = read_green(img[125:170, 740:870], drop_last=True)
    if percent is None or not 0 <= percent <= 100:
        return None, None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    stars = 0
    for x in (650, 800, 950):
        area = hsv[170:190, x-20:x+20]
        stars += int(((area[..., 2] > 190) & (area[..., 1] < 70)).mean() > .25)
    return stars, percent
