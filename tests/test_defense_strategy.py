import unittest
from pathlib import Path
from unittest.mock import patch
from cocbot import config
from cocbot.defense import find_mortars, attack_angle
from cocbot import attack
from cocbot import defense


class DefenseStrategyTests(unittest.TestCase):
    def test_latest_live_village_defenses_detected(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_defenses_variant2.png')
        mortars = find_mortars(image)
        towers = defense.find_defenses(image)
        self.assertTrue(mortars, 'no mortar found in latest real battle')
        self.assertTrue(towers, 'no defense found in latest real battle')

    def test_defense_variants_from_second_missed_battle(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_defenses_variant.png')
        found = defense.find_defenses(image)
        self.assertTrue(any(k == 'wizard_tower' and abs(x - 741) < 20 and abs(y - 373) < 20
                            for k, x, y in found), found)
        self.assertTrue(any(abs(x - 673) < 20 and abs(y - 174) < 20 for x, y in find_mortars(image)))

    def test_mortar_variant_from_missed_live_battle(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_mortar_variant.png')
        found = find_mortars(image)
        self.assertTrue(any(abs(x - 1093) < 20 and abs(y - 423) < 20 for x, y in found), found)

    def test_detects_various_defenses_in_real_enemy(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_defenses.png')
        found = defense.find_defenses(image)
        for kind, point in [('cannon', (701, 380)), ('archer_tower', (838, 258)),
                            ('wizard_tower', (521, 505))]:
            self.assertTrue(any(k == kind and abs(x-point[0]) < 20 and abs(y-point[1]) < 20
                                for k, x, y in found), (kind, found))

    def test_prestrike_uses_other_defenses_when_mortar_not_found(self):
        with patch('cocbot.defense.find_mortars', return_value=[]), \
             patch('cocbot.defense.find_defenses', return_value=[('wizard_tower', 520, 500)]), \
             patch.object(attack, '_card_empty', side_effect=[False, True]), \
             patch.object(attack, 'tap') as tap, patch.object(attack, 'screenshot', return_value=object()), \
             patch.object(attack.time, 'sleep'):
            self.assertEqual((520, 500), attack._prestrike(object(), {'lightning': 0}, lambda s: None))
            self.assertEqual(((520, 500),), (tap.call_args.args,))

    def test_prestrike_targets_recognized_resource_before_blind_center(self):
        with patch('cocbot.defense.find_mortars', return_value=[]), \
             patch('cocbot.defense.find_defenses', return_value=[]), \
             patch.object(attack, 'loot_targets', return_value=[(600, 250, 100)]), \
             patch.object(attack, '_card_empty', side_effect=[False, True]), \
             patch.object(attack, 'save_debug'), patch.object(attack, 'tap') as tap, \
             patch.object(attack, 'screenshot'), patch.object(attack.time, 'sleep'):
            self.assertEqual((600, 250), attack._prestrike(object(), {'lightning': 0}, lambda s: None))
        tap.assert_any_call(600, 250)

    def test_real_enemy_mortars_detected(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_zoomed.png')
        found = find_mortars(image)
        self.assertTrue(any(abs(x - 460) < 35 and abs(y - 510) < 50 for x, y in found), found)
        self.assertTrue(any(abs(x - 775) < 35 and abs(y - 510) < 50 for x, y in found), found)

    def test_normal_home_does_not_produce_false_targets(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'new_home.png')
        self.assertEqual([], find_mortars(image))

    def test_mortar_in_different_zoom_and_village(self):
        image = config.imread(Path(__file__).parent / 'fixtures' / 'enemy_overview.png')
        found = find_mortars(image)
        self.assertTrue(any(abs(x - 895) < 20 and abs(y - 440) < 30 for x, y in found), found)

    def test_attack_angle_points_toward_target(self):
        self.assertAlmostEqual(0, attack_angle((1200, 420)), delta=1)
        self.assertAlmostEqual(180, attack_angle((400, 420)), delta=1)

    def test_prestrike_casts_on_detected_mortar_before_troops(self):
        target = (895, 443)
        calls = []
        with patch('cocbot.defense.find_mortars', return_value=[target]), \
             patch.object(attack, '_card_empty', side_effect=[False, True]), \
             patch.object(attack, 'tap', side_effect=lambda x, y: calls.append((x, y))), \
             patch.object(attack, 'screenshot', return_value=object()), patch.object(attack.time, 'sleep'):
            chosen = attack._prestrike(object(), {'lightning': 0}, lambda s: None)
        self.assertEqual(target, chosen)
        self.assertEqual(target, calls[1])

    def test_prestrike_uses_center_when_no_defense_is_found(self):
        with patch('cocbot.defense.find_mortars', return_value=[]), \
             patch('cocbot.defense.find_defenses', return_value=[]), \
             patch.object(attack, 'loot_targets', return_value=[]), \
             patch.object(attack, '_card_empty', side_effect=[False, True]), \
             patch.object(attack, 'save_debug'), patch.object(attack, 'screenshot'), \
             patch.object(attack.time, 'sleep'), patch.object(attack, 'tap') as tap:
            self.assertEqual(attack.CENTER, attack._prestrike(object(), {'lightning': 0}, lambda s: None))
        tap.assert_any_call(*attack.CENTER)

