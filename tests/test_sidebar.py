import unittest
from pathlib import Path
from unittest.mock import patch

from cocbot import config, menu, upgrade

FIXTURES = Path(__file__).parent / 'fixtures'


class SidebarTests(unittest.TestCase):
    """新版本 UI：右下角"建造"划出来的"建筑和英雄"侧栏。"""

    def test_affordable_king_is_eligible_but_unknown_dark_row_is_not(self):
        rows = menu.read_rows(config.imread(FIXTURES / 'sidebar.png'))
        king = next(r for r in rows if r['cur'] == 'dark' and r['price'] == 5000)
        self.assertEqual('king', king['key'])
        self.assertTrue(upgrade._usable(king, False, False, set()))
        self.assertFalse(upgrade._usable(dict(king, key=None), False, False, set()))
        self.assertFalse(upgrade._usable(dict(king, red=True), False, False, set()))

    def test_king_gets_first_available_builder(self):
        key = upgrade._sort_key('cheap', lambda name: 0)
        king = {'key': 'king', 'price': 15000}
        lab = {'key': 'lab', 'price': 100000}
        self.assertLess(key(king), key(lab))

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

    def test_no_new_building_closes_sidebar_before_deselect(self):
        with patch.object(upgrade, '_open_menu', return_value=True), \
                patch.object(upgrade, '_pages', return_value=iter([(None, [])])), \
                patch.object(upgrade, 'close_menu') as close, \
                patch.object(upgrade, 'tap') as tap:
            self.assertFalse(upgrade._try_build_new(None, {}, lambda msg: None, False))
        close.assert_called_once()
        tap.assert_called_once_with(*upgrade.DESELECT)
