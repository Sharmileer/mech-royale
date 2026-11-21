"""Follow campus ground paths kinematically; no combat, physics or scores simulated."""
import gzip
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arena_map import ArenaMap, Navigator


def main():
    with gzip.open(ROOT / 'check/stream.jsonl.gz', 'rt') as stream:
        hello = json.loads(next(stream))
    world = ArenaMap(ROOT / 'arena', hello['config'])
    rng = random.Random(32)
    free = [i for i, c in enumerate(world.clearance) if c > 5]
    results = []
    for trial in range(12):
        start, original_goal = [world.xy(rng.choice(free)) for _ in range(2)]
        component = world.component[world.cell(*start)]
        end = world.nearest_free(*original_goal, 2.5, component=component)
        if end is None:
            results.append({'case': trial, 'result': 'disconnected ground region; requires jet navigation'})
            continue
        goal = world.xy(end)
        pos = [*start, world.height(*start)]
        nav, blocked = Navigator(world), 0
        for frame in range(1600):
            q = nav.waypoint(pos, goal, 2.5, 9, frame * .1, time.perf_counter() + .004)
            if math.dist(pos[:2], goal) < 6:
                break
            d = math.dist(pos[:2], q)
            if d < .01:
                continue
            step = min(1.17, d)
            end_point = [pos[i] + (q[i] - pos[i]) * step / d for i in range(2)]
            if world.can_move(pos, end_point, 2.5, 9):
                pos = [*end_point, world.height(*end_point)]
            else:
                blocked += 1
            if blocked > 30:
                break
        results.append({'case': trial, 'reached': math.dist(pos[:2], goal) < 6,
                        'remaining_m': round(math.dist(pos[:2], goal), 1), 'steps': frame, 'blocked': blocked})
    print(json.dumps({'kind': 'navigation smoke; not game simulation', 'cases': results}, indent=2))
    if any(r.get('reached') is False for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
