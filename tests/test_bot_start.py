from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cocbot import bot, config


class BotStartTests(unittest.TestCase):
    def test_duplicate_run_does_not_enter_main_or_change_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory) / "bot.pid"
            pid.write_text("12345")
            with patch.object(config, "PID_FILE", pid), \
                    patch.object(bot, "PID_FILE", pid), \
                    patch.object(bot, "main") as main, \
                    patch.object(bot, "log"), \
                    config.bot_lock() as acquired:
                self.assertTrue(acquired)
                with self.assertRaises(SystemExit) as exit:
                    bot.run()
                self.assertEqual(exit.exception.code, 1)
                main.assert_not_called()
                self.assertEqual(pid.read_text(), "12345")

    def test_keyboard_interrupt_cleans_pid_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            pid = Path(directory) / "bot.pid"
            with patch.object(config, "PID_FILE", pid), \
                    patch.object(bot, "PID_FILE", pid), \
                    patch.object(bot, "running_pid", return_value=None), \
                    patch.object(bot, "main", side_effect=KeyboardInterrupt), \
                    patch.object(bot, "log"):
                bot.run()
                self.assertFalse(pid.exists())
                with config.bot_lock() as acquired:
                    self.assertTrue(acquired)


if __name__ == "__main__":
    unittest.main()
