import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cocbot import stats


class StatsTests(unittest.TestCase):
    def test_full_storage_resource_is_excluded_from_score(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(stats, 'BATTLES', Path(tmp) / 'b.csv'):
            gold, elixir, ratio = stats.record('均衡', (1000, 1000), (0, 1000), 30, 0,
                                                (1000, 0), needed=(True, False))
        self.assertEqual((1000, 0, 1.0), (gold, elixir, ratio))

    def test_unreadable_results_are_not_scored_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(stats, 'BATTLES', Path(tmp) / 'b.csv'):
            stats.record('均衡', (1000, 1000), (None, None), 30, 0, (None, None))
            self.assertEqual((0, 0), stats.plan_scores()['均衡'])


if __name__ == '__main__':
    unittest.main()
