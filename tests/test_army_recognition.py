import unittest
from pathlib import Path

from unittest.mock import patch
from cocbot import attack, config, stats


class ArmyRecognitionTests(unittest.TestCase):
    def test_unavailable_practice_plan_uses_practice_scores(self):
        available = {'giant', 'breaker', 'archer', 'goblin', 'wizard'}
        with patch('cocbot.multiplayer_practice.choose_plan', return_value='法师') as choose:
            selected = attack._select_available_plan('戈仑图腾', available, True, lambda _: None)
        self.assertEqual('法师', selected)
        choose.assert_called_once_with(available)

    def test_initial_plan_selected_after_recognition(self):
        available = {'giant', 'breaker', 'archer', 'goblin'}
        with patch('cocbot.multiplayer_practice.choose_plan', return_value='哥布林多'):
            self.assertEqual('哥布林多', attack._select_available_plan(None, available, True, lambda _: None))
    def test_prosperity_overlay_is_closed_before_opening_army(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'prosperity_overlay.png')
        home = config.imread(Path(__file__).parent / 'fixtures' / 'new_home.png')
        self.assertTrue(attack._prosperity_overlay(img))
        self.assertFalse(attack._prosperity_overlay(home))
        with patch.object(attack, 'screenshot', side_effect=[img, home, home, home]), \
             patch.object(attack, 'tap') as tap, patch.object(attack.time, 'sleep'):
            attack._open_army(lambda image, template, threshold: [], {'home_attack': 'home'})
        tap.assert_called_once_with(1425, 56)

    def test_overlay_appearing_during_open_retries_once(self):
        overlay = config.imread(Path(__file__).parent / 'fixtures' / 'prosperity_overlay.png')
        army = config.imread(Path(__file__).parent / 'fixtures' / 'army_current.png')
        tpl = {'army_title': 'title', 'btn_search': 'search'}
        find = lambda img, template, threshold: [(1, 1)] if img is army and template == 'title' else []
        with patch.object(attack, '_open_army', side_effect=[overlay, army]) as op, \
             patch.object(attack, '_army_plan', 'test'), patch.object(attack, '_not_full', return_value=False), \
             patch.object(attack, 'screenshot', return_value=army), patch.object(attack, 'tap') as tap, \
             patch.object(attack.time, 'sleep'):
            self.assertEqual('test', attack.fill_army(find, tpl, lambda msg: None, 'test'))
        self.assertEqual(op.call_count, 2)
        self.assertIn(unittest.mock.call(1425, 56), tap.call_args_list)

    def test_unlocked_wizard_is_recognized(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'army_current.png')
        cards = attack._find_cards(img)
        self.assertIn('wizard', cards)
        self.assertEqual(attack.ARMY_CARDS['wizard'], ('法师', 4))
        self.assertNotIn((607, 615), attack._unknown_cards(img, cards))

    def test_wizard_plan_is_selected_for_an_untried_unlocked_army(self):
        available = {'giant', 'breaker', 'archer', 'goblin', 'wizard'}
        scores = {name: (0.5, 5) for name in stats.PLANS}
        scores['法师'] = (0, 0)
        with patch.object(stats, 'plan_scores', return_value=scores):
            name, _ = stats.choose_plan(available)
        self.assertEqual(name, '法师')
        self.assertIn('wizard', dict(stats.PLANS[name]))

    def test_fill_army_closes_battle_panel_after_army_window(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'army_current.png')
        tpl = {'army_title': 'title', 'btn_search': 'search'}
        def find_all(image, template, threshold):
            return [(100, 100)] if template in ('title', 'search') else []
        with patch.object(attack, '_open_army', return_value=img), \
                patch.object(attack, '_army_plan', 'test'), \
                patch.object(attack, '_not_full', return_value=False), \
                patch.object(attack, 'screenshot', return_value=img), \
                patch.object(attack, 'tap') as tap, patch.object(attack.time, 'sleep'):
            self.assertEqual(attack.fill_army(find_all, tpl, lambda msg: None, 'test'), 'test')
        self.assertEqual(tap.call_args_list[-2:], [unittest.mock.call(1545, 80), unittest.mock.call(1300, 600)])
