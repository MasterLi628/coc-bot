from pathlib import Path
import unittest
from unittest.mock import patch

from cocbot import bot, config, upgrade


FIXTURES = Path(__file__).parent / "fixtures"


class RecoveryTests(unittest.TestCase):
    def test_downloading_update_waits_without_clicks_or_recovery(self):
        img = config.imread(FIXTURES / "game_update.png")
        img[350:450] = 0  # 下载中的页面不再有确认下载提示。
        with patch.object(bot, "screenshot", return_value=img), \
                patch.object(bot, "tap") as tap, patch.object(bot.time, "sleep"), \
                patch.object(bot, "log"), patch.object(bot, "recover") as recover:
            bot.one_round()
        tap.assert_not_called()
        recover.assert_not_called()

    def test_restart_hands_update_page_back_to_main_loop(self):
        img = config.imread(FIXTURES / "game_update.png")
        with patch.object(bot, "screenshot", return_value=img), \
                patch.object(bot, "adb") as adb, patch.object(bot.time, "sleep"), \
                patch.object(bot, "log"):
            self.assertFalse(bot.restart_game())
        self.assertEqual(adb.call_count, 2)

    def test_update_prompt_starts_download_without_restarting(self):
        img = config.imread(FIXTURES / "game_update.png")
        with patch.object(bot, "screenshot", return_value=img), \
                patch.object(bot, "tap") as tap, patch.object(bot.time, "sleep"), \
                patch.object(bot, "log"), patch.object(bot, "recover") as recover:
            bot.one_round()
        tap.assert_called_once_with(485, 778)
        recover.assert_not_called()

    def test_normal_village_is_not_connection_error(self):
        img = config.imread(FIXTURES / "new_home.png")
        self.assertFalse(bot.connection_lost(img))
        self.assertTrue(bot.is_home(img))

    def test_network_dialog_is_not_home(self):
        img = config.imread(FIXTURES / "connection_lost.png")
        self.assertFalse(bot.is_home(img))

    def test_network_dialog_retries_without_village_actions(self):
        img = config.imread(FIXTURES / "connection_lost.png")
        with patch.object(bot, "screenshot", return_value=img), \
                patch.object(bot, "tap") as tap, \
                patch.object(bot.time, "sleep"), \
                patch.object(bot, "log"), \
                patch.object(bot, "pinch_out", side_effect=AssertionError("断线后仍执行村庄操作")) as pinch:
            bot.one_round()
        tap.assert_called_once_with(450, 526)
        pinch.assert_not_called()

    def test_repeated_network_failure_uses_restart_fallback(self):
        img = config.imread(FIXTURES / "connection_lost.png")
        with patch.object(bot, "screenshot", return_value=img), \
                patch.object(bot, "tap"), patch.object(bot.time, "sleep"), \
                patch.object(bot, "log"), \
                patch.object(bot, "restart_game") as restart, \
                patch.object(bot, "pinch_out", side_effect=AssertionError("断线后仍执行村庄操作")):
            for _ in range(3):
                bot.one_round()
        restart.assert_called_once()

    def tearDown(self):
        bot._connection_failures = 0
        bot._last_update_click = 0
        bot._not_home = 0

    def test_upgrade_button_found_below_selected_building(self):
        img = config.imread(FIXTURES / "new_home.png")
        btn = upgrade._find_upgrade_btn(img, bot.TPL)
        self.assertIsNotNone(btn)
        self.assertTrue(700 < btn[0] < 1050)  # 底部那排按钮里靠右的"升级"


if __name__ == "__main__":
    unittest.main()
