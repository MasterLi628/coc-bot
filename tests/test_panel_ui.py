import unittest

from cocbot.panel import latest_log_text


class PanelUiTests(unittest.TestCase):
    def test_latest_log_text_keeps_short_message(self):
        self.assertEqual(latest_log_text("[11-10 12:30:00] 脚本启动"), "最新：12:30  脚本启动")

    def test_latest_log_text_truncates_long_message(self):
        text = latest_log_text("[11-10 12:30:00] " + "异常详情" * 30, limit=12)
        self.assertEqual(text, "最新：12:30  异常详情异常详情异常详情…")

    def test_latest_log_text_handles_missing_timestamp(self):
        self.assertEqual(latest_log_text("日志暂不可用"), "日志暂不可用")


if __name__ == "__main__":
    unittest.main()
