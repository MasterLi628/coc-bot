import unittest
from pathlib import Path

from cocbot import config, menu, upgrade

FIXTURES = Path(__file__).parent / 'fixtures'


class SidebarTests(unittest.TestCase):
    """新版本 UI：右下角"建造"划出来的"建筑和英雄"侧栏。"""

    def test_rows_read_price_and_resource(self):
        rows = menu.read_rows(config.imread(FIXTURES / 'sidebar.png'))
        prices = {(r['cur'], r['price']) for r in rows}
        self.assertIn(('gold', 28000), prices)      # 圣水收集器×5
        self.assertIn(('elixir', 600000), prices)   # 部落城堡
        self.assertIn(('dark', 5000), prices)       # 野蛮人之王

    def test_rows_skip_group_titles_and_hints(self):
        rows = menu.read_rows(config.imread(FIXTURES / 'sidebar.png'))
        # 分组标题、训练营进度条、"新"字标签都不是能点的行
        for r in rows:
            self.assertTrue(r['price'] is not None or r['red'] or r['cur'] == 'gem')

    def test_sidebar_open_only_when_panel_shown(self):
        self.assertTrue(upgrade.sidebar_open(config.imread(FIXTURES / 'sidebar.png')))
        self.assertFalse(upgrade.sidebar_open(config.imread(FIXTURES / 'new_home.png')))
