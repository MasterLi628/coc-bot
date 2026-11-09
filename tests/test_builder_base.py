import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from cocbot import bot, builder_base, config, menu

FIXTURES = Path(__file__).parent / 'fixtures'


class BuilderBaseTests(unittest.TestCase):
    def test_detects_builder_home_but_not_main_village(self):
        self.assertTrue(builder_base.is_home(config.imread(FIXTURES / 'builder_home.png')))
        self.assertFalse(builder_base.is_home(config.imread(FIXTURES / 'builder_home_village.png')))
        self.assertFalse(bot.is_home(config.imread(FIXTURES / 'builder_home.png')))

    def test_no_boat_means_no_blind_tap(self):
        frame = config.imread(FIXTURES / 'builder_home_village.png')
        with patch.object(builder_base, 'screenshot', return_value=frame), \
             patch.object(builder_base, 'pinch_out'), patch.object(builder_base, '_locate', return_value=None), \
             patch.object(builder_base, 'tap') as tap, patch.object(builder_base.time, 'sleep'):
            self.assertFalse(builder_base.enter(lambda _: None))
        tap.assert_not_called()

    def test_identifies_captured_boats(self):
        self.assertIsNotNone(builder_base._locate(config.imread(FIXTURES / 'builder_home_village.png'), 'home_boat.png'))
        self.assertIsNotNone(builder_base._locate(config.imread(FIXTURES / 'builder_home_village_clear.png'), 'home_boat_clear.png'))
        self.assertIsNotNone(builder_base._locate(config.imread(FIXTURES / 'builder_pan.png'), 'builder_boat.png'))
        self.assertIsNotNone(builder_base._locate(config.imread(FIXTURES / 'builder_pan_clear.png'), 'builder_boat_clear.png'))

    def test_builder_find_button_recognized_only_on_attack_menu(self):
        self.assertIsNotNone(builder_base._locate(config.imread(FIXTURES / 'builder_attack_menu.png'), 'builder_find.png'))
        self.assertIsNone(builder_base._locate(config.imread(FIXTURES / 'builder_home.png'), 'builder_find.png'))

    def test_only_current_supported_army_is_allowed(self):
        frame = config.imread(FIXTURES / 'builder_attack_menu.png')
        self.assertTrue(builder_base.supported_army(frame))
        frame[345:435, 375:460] = (255, 0, 0)
        self.assertFalse(builder_base.supported_army(frame))

    def test_resource_bubbles_only_collected_on_builder_home(self):
        frame = config.imread(FIXTURES / 'builder_home.png')
        with patch.object(builder_base, 'screenshot', return_value=frame), \
             patch.object(builder_base, 'tap') as tap, patch.object(builder_base.time, 'sleep'):
            self.assertEqual(2, builder_base.collect_resources(lambda _: None))
        self.assertEqual(2, tap.call_count)

    def test_builder_sidebar_recognizes_research_and_training_camp_prices(self):
        rows = menu.read_rows(config.imread(FIXTURES / 'builder_menu.png'))
        prices = {(r['cur'], r['price'], r['new']) for r in rows}
        self.assertIn(('elixir', 15000, False), prices)
        self.assertIn(('elixir', 4000, False), prices)

    def test_upgrade_candidates_exclude_new_and_unknown_buildings(self):
        frame = config.imread(FIXTURES / 'builder_menu.png')
        candidates = builder_base.upgrade_candidates(frame)
        self.assertEqual({('星空实验室', 15000), ('建筑大师训练营', 4000)},
                         {(item['name'], item['price']) for item in candidates})

    def test_upgrade_does_not_open_menu_outside_builder_base(self):
        with patch.object(builder_base, 'screenshot', return_value=object()), \
             patch.object(builder_base, 'is_home', return_value=False), \
             patch('cocbot.upgrade._open_menu') as open_menu:
            self.assertFalse(builder_base.try_upgrade(lambda _: None))
        open_menu.assert_not_called()

    def test_upgrade_confirmation_requires_matching_price_and_currency(self):
        frame = config.imread(FIXTURES / 'builder_upgrade_dialog.png')
        self.assertIsNotNone(builder_base.upgrade_confirmation(frame, 4000, 'elixir'))
        self.assertIsNone(builder_base.upgrade_confirmation(frame, 5000, 'elixir'))
        self.assertIsNone(builder_base.upgrade_confirmation(frame, 4000, 'gold'))

    def test_upgrade_never_confirms_when_dialog_does_not_match_candidate(self):
        frame = config.imread(FIXTURES / 'builder_upgrade_dialog.png')
        with patch.object(builder_base, 'screenshot', return_value=frame), \
             patch.object(builder_base, 'is_home', return_value=True), \
             patch('cocbot.upgrade._open_menu', return_value=(1, 1)), \
             patch('cocbot.upgrade._safe_shot', return_value=frame), \
             patch.object(builder_base, 'upgrade_candidates', return_value=[
                 {'name': 'Builder Camp', 'price': 4000, 'cur': 'elixir', 'y': 500}]), \
             patch.object(builder_base, 'upgrade_confirmation', return_value=None), \
             patch('cocbot.upgrade.close_windows') as close_windows, \
             patch.object(builder_base, 'tap') as tap:
            self.assertFalse(builder_base.try_upgrade(lambda _: None))
        tap.assert_called_once_with(1280, 510)
        close_windows.assert_called_once()

    def test_start_attack_does_not_search_when_not_on_builder_home(self):
        with patch.object(builder_base, 'screenshot', return_value=object()), \
             patch.object(builder_base, 'is_home', return_value=False), \
             patch.object(builder_base, 'tap') as tap:
            self.assertFalse(builder_base.start_attack(lambda _: None))
        tap.assert_not_called()

    def test_battle_card_rejects_loading_and_accepts_actual_battle(self):
        self.assertFalse(builder_base.battle_ready(config.imread(FIXTURES / 'builder_loading.png')))
        self.assertTrue(builder_base.battle_ready(config.imread(FIXTURES / 'builder_battle.png')))

    def test_builder_result_detected_and_not_confused_with_battle(self):
        result = config.imread(FIXTURES / 'builder_result.png')
        self.assertIsNotNone(builder_base.result_button(result))
        self.assertFalse(builder_base.battle_ready(result))
        self.assertIsNone(builder_base.result_button(config.imread(FIXTURES / 'builder_battle.png')))

    def test_deploy_does_not_touch_result_screen(self):
        frame = config.imread(FIXTURES / 'builder_result.png')
        with patch.object(builder_base, 'screenshot', return_value=frame), \
             patch.object(builder_base, 'tap') as tap:
            self.assertFalse(builder_base.deploy_current(lambda _: None))
        tap.assert_not_called()

    def test_battle_timeout_does_not_click_unknown_screen(self):
        with patch.object(builder_base, 'screenshot', return_value=object()), \
             patch.object(builder_base, 'battle_ready', return_value=False), \
             patch.object(builder_base, 'result_button', return_value=None), \
             patch.object(builder_base.time, 'sleep'), patch.object(builder_base, 'tap') as tap:
            self.assertFalse(builder_base.finish_attack(lambda _: None, ready_checks=2, result_checks=2))
        tap.assert_not_called()

    def test_visit_interval_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(builder_base, 'STATE_FILE', Path(tmp) / 'builder_state.json'):
            self.assertTrue(builder_base.due(now=5000))
            builder_base.reserve(now=5000)
            self.assertFalse(builder_base.due(now=8599))
            self.assertTrue(builder_base.due(now=8600))

    def test_visit_returns_home_after_unsupported_army(self):
        with patch.object(builder_base, 'enter', return_value=True), \
             patch.object(builder_base, 'collect_resources'), \
             patch.object(builder_base, 'try_upgrade'), \
             patch.object(builder_base, 'start_attack', return_value=False), \
             patch.object(builder_base, 'leave', return_value=True) as leave:
            self.assertFalse(builder_base.visit(lambda _: None))
        leave.assert_called_once()

    def test_leave_never_taps_when_boat_not_identified(self):
        frame = config.imread(FIXTURES / 'builder_home.png')
        with patch.object(builder_base, 'screenshot', return_value=frame), \
             patch.object(builder_base, 'pinch_out'), patch.object(builder_base, 'swipe'), \
             patch.object(builder_base, '_locate', return_value=None), \
             patch.object(builder_base, 'is_home', return_value=True), \
             patch.object(builder_base, 'tap') as tap, patch.object(builder_base.time, 'sleep'):
            self.assertFalse(builder_base.leave(lambda _: None))
        tap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
