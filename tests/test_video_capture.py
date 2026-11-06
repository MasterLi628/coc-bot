import unittest
from unittest.mock import patch

from cocbot import adbtool
from cocbot.video_stream import VideoStream


class VideoCaptureTests(unittest.TestCase):
    def test_stream_frame_used_when_available(self):
        frame = object()
        with patch.object(adbtool, '_stream_frame', return_value=frame), \
             patch.object(adbtool, '_screencap') as fallback:
            self.assertIs(adbtool.screenshot(), frame)
            fallback.assert_not_called()

    def test_unavailable_stream_falls_back_to_screencap(self):
        frame = object()
        with patch.object(adbtool, '_stream_frame', return_value=None), \
             patch.object(adbtool, '_screencap', return_value=frame):
            self.assertIs(adbtool.screenshot(), frame)

    def test_input_invalidates_cached_frame(self):
        with patch.object(adbtool, 'adb'), patch.object(adbtool.time, 'monotonic', return_value=123):
            adbtool.tap(10, 20)
        self.assertEqual(123, adbtool._last_input)

    def test_stale_and_pre_input_frames_are_rejected(self):
        reader = VideoStream('adb', 'device')
        reader.frame = b'frame'
        reader.timestamp = 100
        with patch('cocbot.video_stream.time.monotonic', return_value=101):
            self.assertIsNone(reader.latest(max_age=0.5))
        with patch('cocbot.video_stream.time.monotonic', return_value=100.1):
            self.assertIsNone(reader.latest(after=100))

