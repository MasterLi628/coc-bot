import unittest
from pathlib import Path

from cocbot import bot, config


class ResourceTests(unittest.TestCase):
    def test_full_gold_and_elixir(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'resources_full.png')
        self.assertTrue(bot.gold_full(img))
        self.assertTrue(bot.elixir_full(img))
        self.assertTrue(bot.storage_full(img))

    def test_full_gold_and_partially_filled_elixir(self):
        img = config.imread(Path(__file__).parent / 'fixtures' / 'resources_partial.png')
        self.assertTrue(bot.gold_full(img))
        self.assertFalse(bot.elixir_full(img))
        self.assertFalse(bot.storage_full(img))
