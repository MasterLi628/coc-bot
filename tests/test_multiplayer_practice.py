import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cocbot import config, multiplayer_practice as practice


class MultiplayerPracticeTests(unittest.TestCase):
    def test_explores_less_tried_available_plan_periodically(self):
        scores = {k: (0, 0) for k in practice.PLANS}
        scores.update({'均衡': (390, 30), '哥布林多': (320, 3), '巨人多': (310, 3), '弓箭手多': (300, 3)})
        available = {'giant', 'breaker', 'archer', 'goblin'}
        with patch.object(practice, 'scores', return_value=scores):
            self.assertEqual('哥布林多', practice.choose_plan(available))

    def test_read_real_result(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'multiplayer_result_20.png')
        self.assertEqual((0, 20), practice.read_result(img))

    def test_round_limit_persists(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(practice, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(practice, 'FILE', Path(tmp) / 'practice.csv'):
            for n in range(3):
                practice.reserve(now=1000+n)
                practice.record('均衡', 1, 60, now=1000+n)
            self.assertTrue(practice.resting(now=1005))
            self.assertFalse(practice.resting(now=4603))

    def test_three_bad_results_pause_and_scores_exclude_unknown(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(practice, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(practice, 'FILE', Path(tmp) / 'practice.csv'):
            for n in range(3):
                practice.reserve(now=1000+n)
                practice.record('均衡', 0, 15, now=1000+n)
            self.assertTrue(practice.resting(now=1005))
            self.assertGreaterEqual(practice.rest_remaining(now=1005), 3*3600)
            self.assertEqual(3, practice.scores()['均衡'][1])

    def test_failed_or_interrupted_attempts_are_limited(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(practice, 'STATE', Path(tmp) / 'state.json'):
            for n in range(3):
                practice.reserve(now=1000+n)
            self.assertTrue(practice.resting(now=1005))
