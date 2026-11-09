import unittest
from unittest.mock import patch
from pathlib import Path

from cocbot import attack
from cocbot.config import imread


class AttackFollowTests(unittest.TestCase):
    def test_bar_advance_requires_card_to_move_left(self):
        import numpy as np
        img = imread(Path(__file__).parent / 'fixtures' / 'enemy_overview.png')
        shifted = img.copy()
        shifted[775:850, :-300] = img[775:850, 300:]
        attack._XS[:] = [190] * 11 + [1055]
        with patch.object(attack, 'swipe'), patch.object(attack.time, 'sleep'), \
             patch.object(attack, '_in_battle', return_value=True), \
             patch.object(attack, 'screenshot', return_value=shifted):
            self.assertTrue(attack._advance_battle_bar(img, None, {}))
        with patch.object(attack, 'swipe'), patch.object(attack.time, 'sleep'), \
             patch.object(attack, '_in_battle', return_value=True), \
             patch.object(attack, 'screenshot', return_value=img):
            self.assertFalse(attack._advance_battle_bar(img, None, {}))

    def test_scrolled_bar_does_not_move_when_less_than_full(self):
        with patch.object(attack, 'screenshot', return_value=object()), \
             patch.object(attack, '_in_battle', return_value=True), \
             patch.object(attack, 'identify_slots', side_effect=lambda img: attack._XS.__setitem__(slice(None), [180, 300])), \
             patch.object(attack, 'swipe') as swipe:
            attack._deploy_scrolled(None, {}, lambda _: None, [(800, 500)])
        swipe.assert_not_called()

    def test_scrolled_bar_only_deploys_recognized_troops_after_verified_scroll(self):
        import numpy as np
        image = np.zeros((900, 1600, 3), dtype=np.uint8)
        def identify(img):
            attack._XS[:] = list(range(180, 1620, 120))
            return {'barb': 0, 'king': 1, 'lightning': 2}
        with patch.object(attack, 'screenshot', return_value=image), \
             patch.object(attack, '_in_battle', return_value=True), \
             patch.object(attack, 'identify_slots', side_effect=identify), \
             patch.object(attack, '_advance_battle_bar', side_effect=[True, False]), \
             patch.object(attack, '_card_empty', return_value=False), \
             patch.object(attack, '_drop_all') as drop:
            attack._deploy_scrolled(None, {}, lambda _: None, [(800, 500)])
        self.assertEqual(1, drop.call_count)
        self.assertEqual(0, drop.call_args.args[2])

    def test_slot_detection_includes_visible_cards_after_tenth(self):
        img = imread(Path(__file__).parent / 'fixtures' / 'enemy_overview.png')
        # Extend the real battle bar with recognizable card-shaped regions.
        for x in (1091, 1212, 1333, 1454):
            img[748:885, x:x + 110] = img[748:885, 123:233].copy()
        self.assertGreater(len(attack._slot_xs(img)), 10)

    def test_giants_are_followed_by_output_before_remaining_troops(self):
        slots = {'giant': 0, 'breaker': 1, 'archer': 2}
        drops = []
        def drop(find_all, tpl, slot, points, spread, **kwargs):
            drops.append((slot, kwargs.get('max_taps')))
            callback = kwargs.get('on_first')
            if callback:
                callback()
            return False

        logs = []
        with patch.object(attack, 'screenshot', return_value=object()), \
             patch.object(attack, '_pick_angles', return_value=[0]), \
             patch.object(attack, 'identify_slots', return_value=slots), \
             patch.object(attack, '_card_empty', return_value=False), \
             patch.object(attack, '_find_edge', return_value=(800, 500)), \
             patch.object(attack, '_in_battle', return_value=True), \
             patch.object(attack, '_take_reward', return_value=False), \
             patch.object(attack, '_drop_all', side_effect=drop), \
             patch.object(attack, '_cast_spells'), patch.object(attack, '_deploy_rest'), patch.object(attack, '_deploy_scrolled'), \
             patch.object(attack, 'tap'), patch.object(attack.time, 'sleep'):
            attack._deploy(None, {}, logs.append, style='不开口')
        self.assertEqual([(0, 2), (2, 4)], drops[:2])
        self.assertIn(1, [slot for slot, _ in drops[2:]])
        self.assertTrue(any('巨人' in line and '弓箭手' in line and '秒' in line for line in logs))

