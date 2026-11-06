import unittest
from unittest.mock import patch

from cocbot import attack


class AttackFollowTests(unittest.TestCase):
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
             patch.object(attack, '_cast_spells'), patch.object(attack, '_deploy_rest'), \
             patch.object(attack, 'tap'), patch.object(attack.time, 'sleep'):
            attack._deploy(None, {}, logs.append, style='不开口')
        self.assertEqual([(0, 2), (2, 4)], drops[:2])
        self.assertIn(1, [slot for slot, _ in drops[2:]])
        self.assertTrue(any('巨人' in line and '弓箭手' in line and '秒' in line for line in logs))

