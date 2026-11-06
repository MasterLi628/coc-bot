"""Conservative visual detection of known mortar appearance in opponent scenes."""
import math
import cv2
import numpy as np
from .config import TEMPLATES, imread

CENTER = (800, 420)


def find_defenses(image):
    """Find high-confidence defenses for lightning when no mortar is visible."""
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found = []
    for filename in ('wizard_tower', 'wizard_tower_variant', 'wizard_tower_variant2', 'archer_tower',
                     'archer_tower_variant', 'cannon', 'cannon_variant'):
        kind = filename.removesuffix('_variant')
        template = imread(TEMPLATES / 'defenses' / (filename + '.png'))
        if template is None:
            continue
        template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        candidates = []
        for scale in (0.8, 0.9, 1.0, 1.1, 1.2):
            target = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if target.shape[0] >= gray.shape[0] or target.shape[1] >= gray.shape[1]:
                continue
            scores = cv2.matchTemplate(gray, target, cv2.TM_CCOEFF_NORMED)
            scores[:130, :] = 0
            scores[620:, :] = 0
            scores[:, :220] = 0
            scores[:, 1300:] = 0
            ys, xs = np.where(scores >= 0.95)
            for y, x in zip(ys, xs):
                candidates.append((float(scores[y, x]), int(x + target.shape[1] / 2),
                                   int(y + target.shape[0] / 2)))
        for _, x, y in sorted(candidates, reverse=True):
            if all(math.hypot(x - px, y - py) > 35 for _, px, py in found):
                found.append((kind, x, y))
    return found


def find_mortars(image):
    if image is None:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    candidates = []
    for name in ('mortar.png', 'mortar_small.png', 'mortar_variant.png', 'mortar_variant2.png', 'mortar_variant3.png'):
        try:
            template = imread(TEMPLATES / 'defenses' / name)
        except (OSError, ValueError):
            continue
        if template is None:
            continue
        for scale in (0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.85, 1.0, 1.15):
            t = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            tg = cv2.cvtColor(t, cv2.COLOR_BGR2GRAY)
            if min(tg.shape) < 20 or tg.shape[0] >= gray.shape[0] or tg.shape[1] >= gray.shape[1]:
                continue
            match = cv2.matchTemplate(gray, tg, cv2.TM_CCOEFF_NORMED)
            match[:130, :] = 0
            match[650:, :] = 0
            match[:, :220] = 0
            match[:, 1300:] = 0
            ys, xs = np.where(match >= 0.9)
            for y, x in zip(ys, xs):
                candidates.append((float(match[y, x]), int(x + tg.shape[1] / 2), int(y + tg.shape[0] / 2)))
    found = []
    for _, x, y in sorted(candidates, reverse=True):
        if all(math.hypot(x - px, y - py) > 35 for px, py in found):
            found.append((x, y))
    return found


def attack_angle(target):
    x, y = target
    return math.degrees(math.atan2((y - CENTER[1]) / 0.75, x - CENTER[0])) % 360
