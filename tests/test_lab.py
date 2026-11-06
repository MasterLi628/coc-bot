import unittest
from pathlib import Path
from unittest.mock import patch

from cocbot import bot, config, lab


class LabRecognitionTests(unittest.TestCase):
    def test_missing_lab_returns_failure(self):
        home = config.imread(Path(__file__).parent / 'fixtures' / 'new_home.png')
        with patch.object(lab, 'pinch_out'), patch.object(lab, 'tap'), \
             patch.object(lab.time, 'sleep'), patch.object(lab, '_clear_villager_popup', return_value=home), \
             patch.object(lab, '_lab_by_tag', return_value=[]), patch.object(lab, '_lab_candidates', return_value=[]), \
             patch.object(lab, '_lab_from_menu', return_value=None), patch.object(lab, '_safe_shot', return_value=home), \
             patch.object(lab, '_villager_popup', return_value=False), patch.object(lab, 'save_debug'), \
             patch.object(lab, '_find_btn', return_value=None):
            self.assertEqual('fail', lab._try_research(None, None, {'btn_research': home}, lambda message: None))

    def test_villager_popup_is_recognized_and_closed_before_search(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'villager_popup.png')
        self.assertTrue(lab._villager_popup(img))
        home = config.imread(Path(__file__).parent / 'fixtures' / 'new_home.png')
        self.assertFalse(lab._villager_popup(home))
        with patch.object(lab, 'tap') as tap, patch.object(lab.time, 'sleep'), \
             patch.object(lab, '_safe_shot', side_effect=[img, home]):
            self.assertIs(lab._clear_villager_popup(None, None), home)
        tap.assert_called_once_with(815, 70)

    def test_selection_resets_zoom_before_search(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'lab_current.png')
        with patch.object(lab, 'pinch_out') as zoom, patch.object(lab, 'tap'), \
             patch.object(lab.time, 'sleep'), patch.object(lab, '_safe_shot', return_value=img), \
             patch.object(lab, '_lab_by_tag', return_value=[(950, 370)]), \
             patch.object(lab, '_lab_candidates', return_value=[]):
            self.assertIsNotNone(lab._select_lab(lambda *args: [(1, 1)], lambda *args: [], {'label_lab': img}, lambda s: None))
            zoom.assert_called_once_with(2)

    def test_busy_research_window_is_distinguished_from_idle(self):
        busy = config.imread(Path(__file__).parent / 'fixtures' / 'lab_busy_window.png')
        idle = config.imread(Path(__file__).parent / 'fixtures' / 'lab_research_available.png')
        self.assertTrue(lab._busy_window(busy, bot.find_all))
        self.assertFalse(lab._busy_window(idle, bot.find_all))

    def test_busy_lab_candidate_is_tried(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'lab_busy.png')
        points = lab._lab_candidates(img, bot.find_all_scaled)
        self.assertTrue(any(940 <= x <= 990 and 350 <= y + 10 <= 415 for x, y in points[:4]), points[:8])

    def test_available_wizard_card_is_found_and_priced(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'lab_research_available.png')
        cards = lab._colored_cards(img)
        self.assertTrue(any(830 <= x <= 980 and y == 532 for x, y in cards), cards)
        self.assertEqual(120000, lab._card_price(img, *next((x, y) for x, y in cards if 830 <= x <= 980 and y == 532)))

    def test_current_lab_tag_produces_click_on_lab(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'lab_current.png')
        points = lab._lab_by_tag(img, bot.find_all_scaled)
        self.assertTrue(any(920 <= x <= 980 and 350 <= y + 10 <= 390 for x, y in points[:4]), points[:8])

    def test_recent_lab_tag_produces_click_on_lab(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'lab_retry.png')
        points = lab._lab_by_tag(img, bot.find_all_scaled)
        self.assertTrue(any(950 <= x <= 1000 and 400 <= y + 10 <= 440 for x, y in points[:4]), points[:8])
