import unittest
from pathlib import Path
from cocbot import bot, config

FIXTURES = Path(__file__).parent / 'fixtures'

class NewUITests(unittest.TestCase):
    def test_new_home_recognized_and_builder_count(self):
        img = config.imread(FIXTURES / 'new_home.png')
        self.assertTrue(bot.is_home(img))
        self.assertEqual(bot.free_builders(img), 2)

    def test_new_home_is_not_recognized_on_army_screen(self):
        img = config.imread(FIXTURES / 'new_army.png')
        self.assertFalse(bot.is_home(img))
