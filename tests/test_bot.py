"""Behavioral and geometry tests. These do not simulate competition matches."""
import copy
import gzip
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena_map import ArenaMap, Navigator, Prism
from bot import Bot, future_zone
from combat import Combat, aim_solution, intercept_time

with gzip.open(ROOT / 'check/stream.jsonl.gz', 'rt') as stream:
    HELLO = json.loads(next(stream))
    OBS = json.loads(next(stream))


def observation():
    obs = copy.deepcopy(OBS)
    obs['self']['pos'] = [0., 0., 0.]
    obs['self']['aim'] = [0., 0.]
    obs['zone'].update(center=[0, 0], radius=400, next_center=[0, 0], next_radius=250)
    return obs


def enemy(x=100, y=0, kind='mech'):
    return {'id': 4, 'kind': kind, 'pos': [x, y, 0], 'vel': [0, 0, 0],
            'aim': [math.pi, 0], 'hp': 1400, 'max_hp': 1400, 'cubes': 0,
            'weapons': ['gun'], 'charging': False}


def weapon(kind, **extra):
    return {'kind': kind, 'cooldown': 0, 'stats': copy.deepcopy(HELLO['config']['weapons'][kind]), **extra}


def flat_world(folder):
    from array import array
    v2 = Path(folder) / 'v2'
    v2.mkdir()
    data = {'bounds': {'minX': 0, 'maxX': 100, 'minY': 0, 'maxY': 100},
            'terrain': {'file': 'terrain.i16', 'nx': 101, 'ny': 101, 'minX': 0, 'minY': 0, 'step': 1, 'scale': .01},
            'prisms': [{'base': 0, 'top': 22, 'ring': [35, 35, 65, 35, 65, 65, 35, 65], 'holes': []}]}
    (v2 / 'world.json').write_text(json.dumps(data))
    (v2 / 'terrain.i16').write_bytes(array('h', [0] * 10201).tobytes())
    return ArenaMap(folder, HELLO['config'])


class BallisticsTests(unittest.TestCase):
    def test_crossing_target_is_intercepted(self):
        r, v, speed = [100, 0, 0], [0, 20, 0], 252
        t = intercept_time(r, v, speed)
        self.assertAlmostEqual(math.dist([r[i] + v[i] * t for i in range(3)], [0, 0, 0]), speed * t)

    def test_live_projectile_speed_changes_lead(self):
        obs, target = observation(), enemy()
        target['vel'] = [0, 20, 0]
        fast = aim_solution(obs['self'], target, weapon('gun'), HELLO['config'])
        slow = weapon('gun'); slow['stats']['speed'] /= 2
        self.assertGreater(aim_solution(obs['self'], target, slow, HELLO['config'])['aim'][0], fast['aim'][0])

    def test_mortar_parabola_reaches_stationary_target(self):
        me, mortar = observation()['self'], weapon('mortar')
        sol = aim_solution(me, enemy(100), mortar, HELLO['config'])
        t, pitch = sol['flight'], sol['aim'][1]
        end_z = sol['origin'][2] + mortar['stats']['speed'] * math.sin(pitch) * t - .5 * mortar['stats']['gravity'] * t * t
        self.assertAlmostEqual(end_z, sol['target'][2], places=5)

    def test_mortar_unreachable_target(self):
        self.assertIsNone(aim_solution(observation()['self'], enemy(500), weapon('mortar'), HELLO['config']))

    def test_does_not_shoot_before_torso_turns(self):
        obs = observation(); target = enemy(-100)
        result = Combat(HELLO['config'], None).act(obs, target, False, time.perf_counter() + 1)
        self.assertFalse(result['fire'][0])
        self.assertAlmostEqual(abs(result['aim'][0]), math.pi)

    def test_laser_holds_then_releases(self):
        obs = observation(); target = enemy()
        obs['self']['weapons'][1] = weapon('laser', charge=0)
        combat = Combat(HELLO['config'], None)
        a = combat.act(obs, target, False, time.perf_counter() + 1)
        self.assertTrue(a['fire'][1])
        obs['self']['aim'] = a['aim']
        obs['self']['weapons'][1]['charge'] = .3
        self.assertTrue(combat.act(obs, target, False, time.perf_counter() + 1)['fire'][1])
        obs['self']['weapons'][1]['charge'] = .75
        self.assertFalse(combat.act(obs, target, False, time.perf_counter() + 1)['fire'][1])

    def test_mortar_aim_does_not_fire_gun_at_sky(self):
        obs = observation(); target = enemy(150)
        obs['self']['weapons'][1] = weapon('mortar')
        sol = aim_solution(obs['self'], target, obs['self']['weapons'][1], HELLO['config'])
        obs['self']['aim'] = sol['aim']
        action = Combat(HELLO['config'], None).act(obs, target, False, time.perf_counter() + 1)
        self.assertTrue(action['fire'][1])
        self.assertFalse(action['fire'][0])

    def test_recovery_stops_dealing_distant_damage(self):
        obs = observation()
        action = Combat(HELLO['config'], None).act(obs, enemy(), True, time.perf_counter() + 1)
        self.assertEqual(action['fire'], [False, False])

    def test_lightning_does_not_waste_energy_out_of_range(self):
        obs = observation(); obs['self']['weapons'][1] = weapon('lightning')
        self.assertFalse(Combat(HELLO['config'], None).act(obs, enemy(80), False, time.perf_counter() + 1)['fire'][1])

    def test_lightning_preserves_reserve_energy(self):
        obs = observation(); obs['self']['weapons'][1] = weapon('lightning'); obs['self']['energy'] = 3
        self.assertFalse(Combat(HELLO['config'], None).act(obs, enemy(20), False, time.perf_counter() + 1)['fire'][1])


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.world = flat_world(cls.folder.name)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_building_blocks_ray(self):
        self.assertFalse(self.world.line_clear([10, 50, 7], [90, 50, 7]))
        self.assertTrue(self.world.line_clear([10, 50, 25], [90, 50, 25]))

    def test_bridge_clears_ray_below(self):
        p = Prism({'base': 12, 'top': 16, 'ring': [0, 0, 20, 0, 20, 20, 0, 20], 'holes': []})
        self.assertFalse(p.intersects_ray([-10, 10, 7], [30, 10, 7]))
        self.assertTrue(p.intersects_ray([-10, 10, 14], [30, 10, 14]))

    def test_courtyard_hole_is_not_solid(self):
        p = Prism({'base': 0, 'top': 20, 'ring': [0, 0, 20, 0, 20, 20, 0, 20],
                   'holes': [[5, 5, 15, 5, 15, 15, 5, 15]]})
        self.assertFalse(p.intersects_ray([7, 10, 7], [13, 10, 7]))
        self.assertTrue(p.intersects_ray([-5, 10, 7], [25, 10, 7]))

    def test_body_clearance_not_just_center(self):
        self.assertFalse(self.world.can_move([30, 20, 0], [34, 50], 2.5, 9))
        self.assertTrue(self.world.can_move([28, 20, 0], [28, 70], 2.5, 9))

    def test_astar_routes_around_building(self):
        nav = Navigator(self.world)
        for i in range(30):
            nav.waypoint([10, 50, 0], [90, 50], 2.5, 9, i * .1, time.perf_counter() + .02)
            if nav.path:
                break
        self.assertTrue(nav.path)
        self.assertGreater(len(nav.path), 20)
        pts = [self.world.xy(c) for c in nav.path]
        for a, b in zip(pts, pts[1:]):
            self.assertTrue(self.world.can_move([*a, 0], b, 2.5, 9), (a, b))

    def test_zone_hold_and_shrink(self):
        zone = {'center': [0, 0], 'radius': 100, 'next_center': [20, 0], 'next_radius': 40,
                'shrink_start': 10, 'shrink_end': 30}
        self.assertEqual(future_zone(zone, 0, 5), ([0, 0], 100))
        self.assertEqual(future_zone(zone, 0, 20), ([10, 0], 70))
        current = {**zone, 'center': [10, 0], 'radius': 70}
        self.assertEqual(future_zone(current, 20, 5), ([15, 0], 55))
        self.assertEqual(future_zone(current, 20, 20), ([20, 0], 40))


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        flat_world(cls.folder.name)
        cls.hello = copy.deepcopy(HELLO)
        cls.hello['map']['dir'] = cls.folder.name

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        self.bot = Bot(self.hello)
        self.obs = observation()
        self.obs['self']['pos'] = [10, 10, 0]

    def test_takes_weapon_into_empty_slot(self):
        self.obs['pickups'] = [{'id': 8001, 'kind': 'laser', 'pos': [11, 10, 0]}]
        self.assertEqual(self.bot._pickup(self.obs, []), {'take': 8001})

    def test_does_not_take_unreachable_roof_loot(self):
        self.obs['pickups'] = [{'id': 8001, 'kind': 'laser', 'pos': [11, 10, 20]}]
        self.assertEqual(self.bot._pickup(self.obs, []), {})

    def test_cannot_swap_charging_laser(self):
        self.obs['self']['weapons'][1] = weapon('laser', charge=.5)
        self.obs['zone']['radius'] = 50
        self.obs['pickups'] = [{'id': 8001, 'kind': 'lightning', 'pos': [11, 10, 0]}]
        self.assertEqual(self.bot._pickup(self.obs, []), {})

    def test_no_enemies_is_valid_and_finite(self):
        action = self.bot.act(self.obs)
        self.assertEqual(action['tick'], self.obs['tick'])
        self.assertLessEqual(math.hypot(*action['move']), 1.00001)
        self.assertTrue(all(math.isfinite(v) for v in action['aim']))
        json.dumps(action, allow_nan=False)

    def test_deadline_does_not_produce_invalid_action(self):
        self.obs['mechs'] = [dict(enemy(70 + i, 80), id=i) for i in range(15)]
        self.obs['self']['hp'] = 300
        action = self.bot.act(self.obs)
        json.dumps(action, allow_nan=False)
        self.assertEqual(len(action['fire']), 2)
        self.assertTrue(self.bot.recovering)

    def test_upgrade_chooses_known_survival_benefit(self):
        self.obs['offer'] = {'choices': [{'id': 'sensors'}, {'id': 'armor'}, {'id': 'laser_pierce'}]}
        self.assertEqual(self.bot._upgrade(self.obs), 1)

    def test_stays_behind_cover_to_regenerate(self):
        self.obs['self']['pos'] = [25, 50, 0]
        self.bot.goal = [25, 50]
        self.bot.recovering = True
        foes = [enemy(75, 50)]
        result = self.bot._move(self.obs, foes, foes[0], time.perf_counter() + .1)
        self.assertEqual(result['move'], [0, 0])


if __name__ == '__main__':
    unittest.main()
