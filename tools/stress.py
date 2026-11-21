"""Generated observation stress test; NOT a simulator or placement benchmark."""
import copy
import gzip
import json
import math
from pathlib import Path
import random
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'check'))
from bot import Bot
from check import Schema


def main():
    with gzip.open(ROOT / 'check/stream.jsonl.gz', 'rt') as stream:
        h, original = json.loads(next(stream)), json.loads(next(stream))
    h['map']['dir'] = str(ROOT / 'arena')
    bot = Bot(h)
    validator = Schema(str(ROOT / 'mechfight.schema.json'))
    rng = random.Random(834031)
    free = [i for i, c in enumerate(bot.world.clearance) if c > 7]
    times, fire = [], {k: 0 for k in ['gun', 'laser', 'mortar', 'lightning']}
    for i in range(800):
        obs = copy.deepcopy(original)
        obs['tick'], obs['t'] = i * 2, i * .1
        me = obs['self']
        x, y = bot.world.xy(rng.choice(free))
        me['pos'] = [x, y, bot.world.height(x, y)]
        me['vel'] = [rng.uniform(-12, 12), rng.uniform(-12, 12), 0]
        me['hp'] = rng.uniform(250, 1400)
        me['energy'] = rng.uniform(2, 100)
        me['aim'] = [rng.uniform(-math.pi, math.pi), rng.uniform(-.6, 1.2)]
        kind = ['laser', 'mortar', 'lightning'][i % 3]
        me['weapons'][1] = {'kind': kind, 'cooldown': 0, 'stats': copy.deepcopy(h['config']['weapons'][kind])}
        if kind == 'laser':
            me['weapons'][1]['charge'] = rng.choice([0, .2, .75])
        obs['mechs'] = []
        for j in range(15):
            angle, distance = rng.random() * math.tau, rng.uniform(20, 210)
            ex, ey = x + math.cos(angle) * distance, y + math.sin(angle) * distance
            obs['mechs'].append({'id': j if j < 3 else j + 1, 'kind': 'mech', 'pos': [ex, ey, bot.world.height(ex, ey)],
                                 'vel': [rng.uniform(-15, 15), rng.uniform(-15, 15), 0],
                                 'aim': [angle + math.pi, 0], 'hp': rng.uniform(50, 1800), 'max_hp': 1960,
                                 'cubes': j, 'weapons': ['gun', ['laser', 'mortar', 'lightning'][j % 3]], 'charging': j % 4 == 0})
        obs['projectiles'] = [{'id': j, 'kind': 'mortar' if j % 3 == 0 else 'gun',
                               'pos': [x + rng.uniform(-50, 50), y + rng.uniform(-50, 50), me['pos'][2] + rng.uniform(0, 25)],
                               'vel': [rng.uniform(-250, 250), rng.uniform(-250, 250), rng.uniform(-25, 10)]} for j in range(36)]
        obs['zone']['center'] = [x, y]
        obs['zone']['next_center'] = [x + 20, y + 20]
        obs['zone']['radius'] = 120 if i % 5 else 20
        obs['zone']['next_radius'] = 70 if i % 5 else 0
        if i % 7 == 0:
            obs['offer'] = {'id': 1, 'expires': obs['t'] + 5, 'choices': [{'id': x, 'name': x, 'desc': ''} for x in ['armor', 'gun_damage', 'servos']]}
        started = time.perf_counter()
        action = bot.act(obs)
        times.append((time.perf_counter() - started) * 1000)
        errors = validator.errors(action, 'Action')
        if errors:
            raise AssertionError(errors)
        json.dumps(action, allow_nan=False)
        if math.hypot(*action.get('move', [0, 0])) > 1.000001:
            raise AssertionError('oversized movement')
        for slot, fired in enumerate(action['fire']):
            if fired:
                fire[me['weapons'][slot]['kind']] += 1
    times.sort()
    result = {'kind': 'synthetic stress; not gameplay', 'observations': len(times), 'schema_errors': 0,
              'ms_p50': times[len(times) // 2], 'ms_p99': times[int(len(times) * .99)], 'ms_max': max(times),
              'max_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'trigger_true_counts': fire}
    print(json.dumps(result, indent=2))
    if max(times) > 50:
        raise SystemExit('Some decisions exceeded 50 ms; investigate before submission.')


if __name__ == '__main__':
    main()
