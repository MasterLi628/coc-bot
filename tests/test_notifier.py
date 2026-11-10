import json
import unittest
from unittest.mock import patch

from cocbot import notifier


class NotifierTests(unittest.TestCase):
    def test_pushplus_payload_is_encoded_and_token_is_not_in_error(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"code":200,"msg":"ok"}'

        with patch.object(notifier.request, "urlopen", return_value=Response()) as open_url:
            result = notifier.send("secret-token", "Test", "hello")
        self.assertTrue(result.ok)
        req = open_url.call_args.args[0]
        self.assertEqual(req.full_url, notifier.API_URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), {
            "token": "secret-token", "title": "Test", "content": "hello",
            "channel": "wechat", "template": "markdown",
        })

    def test_blank_token_fails_without_network(self):
        with patch.object(notifier.request, "urlopen") as open_url:
            result = notifier.send("  ", "Test", "hello")
        self.assertFalse(result.ok)
        self.assertIn("Token", result.message)
        open_url.assert_not_called()

    def test_only_important_log_lines_are_notifications(self):
        self.assertIsNotNone(notifier.event_for("本场抢到 金币 10 / 圣水 20"))
        self.assertIsNotNone(notifier.event_for("出错了：ADB timeout"))
        self.assertIsNone(notifier.event_for("等待 30 秒后继续"))


if __name__ == "__main__":
    unittest.main()
