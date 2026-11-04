import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
from cocbot import attack, bot, config, scout

class DarkElixirTests(unittest.TestCase):
    def test_full_dark_storage(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'resources_full.png')
        self.assertTrue(bot.dark_elixir_full(img))

    def test_partial_dark_storage(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'resources_full.png')
        img[160:170, 1338:1350] = (30, 20, 30)
        self.assertFalse(bot.dark_elixir_full(img))
        self.assertFalse(bot.storage_full(img))

    def test_reads_opponent_dark_elixir(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'opponent_dark.png')
        self.assertEqual(298, scout.read_dark_loot(img))

    def test_dark_only_target(self):
        with patch.object(scout, 'read_loot', return_value=(500000, 500000)), patch.object(scout, 'read_dark_loot', return_value=298):
            self.assertTrue(scout.worth_attacking(None, False, False, 40000, 40000, True, 250)[0])
            self.assertFalse(scout.worth_attacking(None, False, False, 40000, 40000, True, 300)[0])

    def test_unreadable_dark_loot_does_not_match(self):
        with patch.object(scout, 'read_loot', return_value=(500000, 500000)), patch.object(scout, 'read_dark_loot', return_value=None):
            self.assertFalse(scout.worth_attacking(None, False, False, 40000, 40000, True, 250)[0])

    def test_does_not_end_battle_while_dark_loot_remains(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'opponent_dark.png')
        calls = iter((False, False, True))
        def find_all(image, template, threshold):
            if template == 'result':
                return [(1, 1)] if next(calls) else []
            return []
        tpl = {name: name for name in ('btn_return', 'btn_claim', 'result_label', 'btn_end_battle')}
        tpl['result_label'] = 'result'
        with patch.object(attack, 'screenshot', return_value=img), patch.object(attack, 'read_loot', return_value=(0, 0)), \
             patch.object(attack, 'read_dark_loot', return_value=298), patch.object(attack, '_take_end_reward', return_value=False), \
             patch.object(attack, '_take_reward', return_value=False), patch.object(attack.time, 'sleep'), patch.object(attack, 'tap') as tap:
            attack._wait_battle_end(find_all, tpl, lambda message: None, need_dark=True)
            tap.assert_not_called()
