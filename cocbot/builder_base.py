"""建筑大师基地独立界面识别和安全导航。"""
import time
import json

import cv2

from .adbtool import screenshot, swipe, tap
from .config import BASE, TEMPLATES, imread
from .pinch import pinch_out

STATE_FILE = BASE / 'builder_base_state.json'
INTERVAL = 3600


def due(now=None):
    now = time.time() if now is None else now
    try:
        last = json.loads(STATE_FILE.read_text(encoding='utf-8'))['last_attempt']
        return now - float(last) >= INTERVAL
    except (OSError, ValueError, TypeError, KeyError):
        return True


def reserve(now=None):
    now = time.time() if now is None else now
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps({'last_attempt': now}), encoding='utf-8')
    tmp.replace(STATE_FILE)


def _locate(img, name, threshold=0.87, scales=(1.0,)):
    template = imread(TEMPLATES / name)
    if template is None or img is None:
        return None
    if template.shape[0] > img.shape[0] or template.shape[1] > img.shape[1]:
        return None
    best = (threshold, None)
    for scale in scales:
        candidate = template if scale == 1.0 else cv2.resize(template, None, fx=scale, fy=scale)
        score = cv2.matchTemplate(img, candidate, cv2.TM_CCOEFF_NORMED)
        _, value, _, point = cv2.minMaxLoc(score)
        if value > best[0]:
            best = (value, (point[0] + candidate.shape[1] // 2, point[1] + candidate.shape[0] // 2))
    return best[1]


def is_home(img):
    return _locate(img, 'builder_attack_home.png', 0.85) is not None


def battle_ready(img):
    # 先支持当前账号的狂暴野蛮人；加载动画或其他未知部队不能盲点。
    card = _locate(img, 'builder_barb.png', 0.9)
    return card is not None and card[1] > 760


def result_button(img):
    return _locate(img, 'builder_result_return.png', 0.9)


def supported_army(img):
    if _locate(img, 'builder_menu_barb.png', 0.9) is None:
        return False
    hsv = cv2.cvtColor(img[345:435, 375:925], cv2.COLOR_BGR2HSV)
    return float((hsv[..., 1] > 120).mean()) < 0.04


def collect_resources(log):
    img = screenshot()
    if not is_home(img):
        return 0
    count = 0
    for name in ('builder_collect_gold.png', 'builder_collect_elixir.png'):
        # Only look on the map, never on the resource counters or shop buttons.
        point = _locate(img[120:720, 160:1400], name, 0.96)
        if point:
            tap(point[0] + 160, point[1] + 120)
            count += 1
            time.sleep(0.4)
    log(f'建筑大师基地：收集 {count} 处资源')
    return count


def upgrade_candidates(img):
    from .menu import read_rows
    rows = read_rows(img)
    candidates = []
    for name, filename in (('建筑大师训练营', 'builder_name_camp.png'),
                           ('星空实验室', 'builder_name_lab.png')):
        point = _locate(img[:, 1163:], filename, 0.91)
        if not point:
            continue
        row = min(rows, key=lambda item: abs(item['y'] - point[1]), default=None)
        if row and abs(row['y'] - point[1]) <= 15 and not row['new'] and not row['red'] \
                and row['cur'] in ('gold', 'elixir') and row['price']:
            candidates.append(dict(row, name=name))
    return candidates


def upgrade_confirmation(img, expected_price, expected_currency):
    from .menu import _cursor
    from .scout import read_number
    if not _locate(img, 'builder_upgrade_label.png', 0.9):
        return None
    hsv = cv2.cvtColor(img[725:850, 660:955], cv2.COLOR_BGR2HSV)
    green = ((hsv[..., 0] >= 35) & (hsv[..., 0] <= 85) & (hsv[..., 1] > 100) & (hsv[..., 2] > 150)).mean()
    icon = cv2.cvtColor(img[750:825, 880:940], cv2.COLOR_BGR2HSV)
    price_img = cv2.resize(img[755:820, 715:875], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    if green < 0.3 or _cursor(icon) != expected_currency or read_number(price_img) != expected_price:
        return None
    return (800, 790)


def try_upgrade(log):
    from . import bot, upgrade
    if not is_home(screenshot()):
        return False
    try:
        if upgrade._open_menu(bot.find_all, bot.TPL) is None:
            return False
        candidates = upgrade_candidates(upgrade._safe_shot(bot.find_all, bot.TPL))
        if not candidates:
            upgrade._leave(bot.find_all, bot.TPL)
            return False
        chosen = candidates[0]
        log(f"建筑大师基地：尝试升级{chosen['name']}（{chosen['price']}）")
        tap(1280, chosen['y'] + 10)
        time.sleep(2)
        img = upgrade._safe_shot(bot.find_all, bot.TPL)
        button = upgrade_confirmation(img, chosen['price'], chosen['cur'])
        if button is None:
            log(f"建筑大师基地：升级确认与{chosen['name']}的价格或资源不匹配，取消")
            upgrade.close_windows(bot.find_all, bot.find_all_scaled, bot.TPL, log)
            return False
        tap(*button)
        time.sleep(2)
        after = upgrade._safe_shot(bot.find_all, bot.TPL)
        if _locate(after, 'builder_upgrade_label.png', 0.9):
            log('建筑大师基地：升级确认框未关闭，放弃本次操作')
            upgrade.close_windows(bot.find_all, bot.find_all_scaled, bot.TPL, log)
            return False
        upgrade.close_menu(bot.find_all, bot.TPL)
        if not is_home(screenshot()):
            log('建筑大师基地：升级后界面状态未确认')
            return False
        log(f"建筑大师基地：已确认开始升级{chosen['name']}（{chosen['price']}）")
        return True
    except (upgrade.Danger, upgrade.Locked, upgrade.Warned):
        log('建筑大师基地：升级条件或价格未确认，取消')
        upgrade.close_windows(bot.find_all, bot.find_all_scaled, bot.TPL, log)
        return False


def deploy_current(log):
    from . import attack
    img = screenshot()
    if result_button(img) or not battle_ready(img):
        return False
    card = _locate(img, 'builder_barb.png', 0.9)
    if card is None:
        return False
    x = card[0]
    tap(x, 815)
    time.sleep(0.3)
    before = cv2.cvtColor(img[742:778, x - 55:x + 55], cv2.COLOR_BGR2GRAY)
    point = None
    for candidate in ((530, 550), (400, 450), (1100, 550), (1200, 450), (800, 650)):
        current = screenshot()
        if result_button(current):
            return False
        tap(*candidate)
        time.sleep(0.5)
        current = screenshot()
        if result_button(current):
            return True
        after = cv2.cvtColor(current[742:778, x - 55:x + 55], cv2.COLOR_BGR2GRAY)
        if attack._card_changed(before, after):
            point = candidate
            break
    if point is None:
        log('建筑大师基地：未找到可确认的下兵位置')
        return False
    for _ in range(5):
        img = screenshot()
        if result_button(img):
            break
        hsv = cv2.cvtColor(img[760:870, x - 50:x + 50], cv2.COLOR_BGR2HSV)
        if float(hsv[..., 1].mean()) < 25:
            break
        tap(*point)
        time.sleep(0.5)
    log('建筑大师基地：已尝试部署狂暴野蛮人')
    return True


def enter(log):
    from . import bot
    img = screenshot()
    if is_home(img):
        return True
    if not bot.is_home(img):
        log('建筑大师基地：当前不在主村，取消前往')
        return False
    pinch_out(2)
    time.sleep(1)
    img = screenshot()
    scales = (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)
    boat = (_locate(img, 'home_boat_clear.png', 0.84, scales)
            or _locate(img, 'home_boat_variant.png', 0.84, scales)
            or _locate(img, 'home_boat.png', 0.84, scales))
    if boat is None:
        log('建筑大师基地：没有可靠识别到船')
        return False
    tap(*boat)
    for _ in range(20):
        time.sleep(1)
        if is_home(screenshot()):
            return True
    log('建筑大师基地：进入后未识别到基地')
    return False


def leave(log):
    from . import bot
    img = screenshot()
    if not is_home(img):
        if bot.is_home(img):
            return True
        log('建筑大师基地：当前不在基地界面，无法返回')
        return False
    boat = _locate(img, 'builder_boat_clear.png', scales=(0.9, 0.95, 1.0, 1.05, 1.1, 1.15)) or _locate(img, 'builder_boat.png')
    if boat is None:
        pinch_out(2)
        time.sleep(1)
        swipe(800, 350, 800, 700, 500)
        time.sleep(1)
        img = screenshot()
        boat = _locate(img, 'builder_boat_clear.png', scales=(0.9, 0.95, 1.0, 1.05, 1.1, 1.15)) or _locate(img, 'builder_boat.png')
    if boat is None:
        log('建筑大师基地：没有可靠识别到返程船')
        return False
    tap(*boat)
    for _ in range(20):
        time.sleep(1)
        img = screenshot()
        if not is_home(img) and bot.is_home(img):
            return True
    log('建筑大师基地：返程后未识别到主村')
    return False


def start_attack(log):
    img = screenshot()
    if not is_home(img):
        log('建筑大师基地：未识别到基地，取消进攻')
        return False
    button = _locate(img, 'builder_attack_home.png', 0.85)
    if button is None:
        return False
    tap(*button)
    for _ in range(10):
        time.sleep(1)
        img = screenshot()
        button = _locate(img, 'builder_find.png')
        if button:
            if not supported_army(img):
                log('建筑大师基地：当前部队尚未适配，跳过进攻')
                tap(1360, 165)  # 已识别的进攻窗口关闭按钮
                return False
            tap(*button)
            return True
    log('建筑大师基地：未识别到寻找对手按钮')
    return False


def finish_attack(log, ready_checks=40, result_checks=200):
    ready = False
    for _ in range(ready_checks):
        time.sleep(1)
        img = screenshot()
        if battle_ready(img):
            ready = True
            break
        if result_button(img):
            break
    if ready:
        deploy_current(log)
    else:
        log('建筑大师基地：未识别到已支持的部队，等待结算')
    for _ in range(result_checks):
        img = screenshot()
        button = result_button(img)
        if button:
            tap(*button)
            for _ in range(20):
                time.sleep(1)
                if is_home(screenshot()):
                    log('建筑大师基地：战斗结束并回营')
                    return True
            break
        time.sleep(1)
    log('建筑大师基地：未能确认结算或回营')
    return False


def visit(log):
    if not enter(log):
        return False
    completed = False
    try:
        collect_resources(log)
        try_upgrade(log)
        if start_attack(log):
            completed = finish_attack(log)
    finally:
        returned = leave(log)
    return completed and returned
