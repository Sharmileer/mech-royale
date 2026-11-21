"""Weapon-specific aiming. All four weapons share one torso aim."""
from __future__ import annotations
import math
import time
from arena_map import clamp


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def length(v):
    return math.sqrt(sum(x * x for x in v))


def intercept_time(relative, velocity, speed):
    """Positive root of |relative + velocity*t| = speed*t, or safe fallback."""
    a = sum(v * v for v in velocity) - speed * speed
    b = 2 * sum(r * v for r, v in zip(relative, velocity))
    c = sum(r * r for r in relative)
    if abs(a) < 1e-8:
        return max(0, -c / b) if b < -1e-8 else math.sqrt(c) / max(speed, 1)
    disc = b * b - 4 * a * c
    if disc >= 0:
        roots = [t for t in ((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a)) if t > 0]
        if roots:
            return min(roots)
    return math.sqrt(c) / max(speed, 1)


def dimensions(target, config):
    kind = target.get('kind', 'crate')
    if kind == 'mech':
        scale = 1 + config['cubes'].get('size_per_cube', .05) * target.get('cubes', 0)
        return config['mech']['radius'] * scale, config['mech']['height'] * scale
    if kind == 'npc':
        return config['npc'].get('radius', 1.8), config['npc'].get('height', 3.5)
    return config['crates'].get('radius', 1.4), config['crates'].get('height', 2.4)


def aim_solution(me, target, weapon, config, high=False):
    stats = weapon['stats']
    kind = weapon['kind']
    own = me['stats']
    step = 1 / config['match']['tick_hz']
    # World-frame projectiles: no undocumented inherited shooter velocity.
    origin = [me['pos'][i] + me['vel'][i] * step for i in range(3)]
    origin[2] += own['height'] + .5 if kind == 'mortar' else own['muzzle_height']
    radius, height = dimensions(target, config)
    tp = list(target['pos'])
    tp[2] += min(height * .5, 2.0) if kind == 'mortar' else height * .5
    vel = target.get('vel', [0, 0, 0])
    tp = [tp[i] + vel[i] * step for i in range(3)]
    flight = 0.0
    if kind == 'gun':
        flight = intercept_time([tp[i] - origin[i] for i in range(3)], vel, stats['speed'])
        flight = max(0.0, flight - 3 / stats['speed'])
        tp = [tp[i] + vel[i] * flight for i in range(3)]
    elif kind == 'mortar':
        speed, gravity = stats['speed'], stats['gravity']
        base = list(tp)
        for _ in range(5):
            tp = [base[i] + vel[i] * flight for i in range(3)]
            d = math.hypot(tp[0] - origin[0], tp[1] - origin[1])
            dz = tp[2] - origin[2]
            disc = speed ** 4 - gravity * (gravity * d * d + 2 * dz * speed * speed)
            if disc < 0 or d < .1:
                return None
            pitch = math.atan2(speed * speed + (1 if high else -1) * math.sqrt(disc), gravity * d)
            flight = d / max(.1, speed * math.cos(pitch))
        if flight > stats.get('ttl', 7) or not own['min_pitch'] <= pitch <= own['max_pitch']:
            return None
        return {'aim': [math.atan2(tp[1] - origin[1], tp[0] - origin[0]), pitch],
                'origin': origin, 'target': tp, 'flight': flight, 'distance': d,
                'radius': radius, 'height': height, 'kind': kind}
    dx, dy, dz = (tp[i] - origin[i] for i in range(3))
    distance = math.hypot(dx, dy)
    pitch = math.atan2(dz, distance)
    if not own['min_pitch'] <= pitch <= own['max_pitch']:
        return None
    return {'aim': [math.atan2(dy, dx), pitch], 'origin': origin, 'target': tp,
            'flight': flight, 'distance': distance, 'radius': radius, 'height': height, 'kind': kind}


def aligned(me, solution, config, lightning=False):
    step = 1 / config['match']['tick_hz']
    yaw, pitch = solution['aim']
    # Estimate only the next server tick's slew, never assume instant aiming.
    yaw_error = max(0, abs(wrap(yaw - me['aim'][0])) - me['stats']['turn_rate'] * step)
    pitch_error = max(0, abs(pitch - me['aim'][1]) - me['stats']['pitch_rate'] * step)
    distance = max(1, solution['distance'])
    yaw_limit = math.atan2(solution['radius'] * .85, distance)
    pitch_limit = math.atan2(solution['height'] * .38, distance)
    return yaw_error <= yaw_limit and (lightning or pitch_error <= pitch_limit)


class Combat:
    def __init__(self, config, world):
        self.cfg, self.world = config, world
        self.lock = None
        self.lock_until = 0.0
        self.last_target = None

    def clear(self, solution):
        return not self.world or self.world.line_clear(solution['origin'], solution['target'])

    def arc_clear(self, sol, stats, deadline):
        if not self.world:
            return True
        yaw, pitch = sol['aim']
        speed, gravity = stats['speed'], stats['gravity']
        vx = speed * math.cos(pitch) * math.cos(yaw)
        vy = speed * math.cos(pitch) * math.sin(yaw)
        vz = speed * math.sin(pitch)
        a = sol['origin']
        # Start at the tube and test the complete barrel/flight trajectory.
        n = min(24, max(4, math.ceil(sol['flight'] / .15)))
        for i in range(1, n + 1):
            if time.perf_counter() > deadline:
                return False
            t = sol['flight'] * i / n
            b = [sol['origin'][0] + vx * t, sol['origin'][1] + vy * t,
                 sol['origin'][2] + vz * t - .5 * gravity * t * t]
            if not self.world.line_clear(a, b):
                return False
            a = b
        return True

    def act(self, obs, target, recovering, deadline):
        me, now = obs['self'], obs['t']
        action = {'fire': [False, False]}
        if target is None:
            self.lock = None
            return action
        weapons = me['weapons']
        distance = math.dist(me['pos'][:2], target['pos'][:2])
        is_mech = target.get('kind') == 'mech'
        # Let disengagement actually start regeneration; don't plink distant foes.
        can_attack = not recovering or (is_mech and distance < 34)
        solutions = {}
        for i, weapon in enumerate(weapons):
            if weapon:
                sol = aim_solution(me, target, weapon, self.cfg)
                if sol:
                    solutions[i] = sol
        if not solutions:
            return action
        selected = None
        # Finish an existing laser charge before changing firing geometry.
        for i, weapon in enumerate(weapons):
            if weapon and weapon['kind'] == 'laser' and weapon.get('charge', 0) > 0 and i in solutions:
                selected = i
                break
        # A short mortar aiming commitment prevents the gun from stealing aim every frame.
        if selected is None and self.lock and self.lock[1] == target['id'] and now < self.lock_until:
            i = self.lock[0]
            if i in solutions and weapons[i]['kind'] == 'mortar' and weapons[i]['cooldown'] <= .1:
                selected = i
        if selected is None and can_attack:
            for i, weapon in enumerate(weapons):
                if not weapon or i not in solutions or weapon['cooldown'] > .1:
                    continue
                kind, stats, sol = weapon['kind'], weapon['stats'], solutions[i]
                if kind == 'lightning' and distance < stats['range'] - 2 and me['energy'] > stats['energy'] + 12:
                    selected = i
                    break
                if kind == 'laser' and distance > 55 and distance < stats['range'] and self.clear(sol):
                    selected = i
                    break
                if kind == 'mortar' and distance > max(24, stats['splash_radius'] * 2 + 5) and is_mech:
                    if self.arc_clear(sol, stats, deadline):
                        selected = i
                        self.lock = (i, target['id'])
                        self.lock_until = now + .7
                        break
        if selected is None:
            selected = next((i for i in solutions if weapons[i]['kind'] == 'gun'), next(iter(solutions)))
        primary = solutions[selected]
        action['aim'] = primary['aim']
        if not can_attack:
            return action
        for i, weapon in enumerate(weapons):
            if not weapon or i not in solutions:
                continue
            sol, stats, kind = solutions[i], weapon['stats'], weapon['kind']
            # Readiness depends on the common commanded aim, as well as actual aim.
            same_yaw = abs(wrap(primary['aim'][0] - sol['aim'][0])) <= math.atan2(sol['radius'] * .8, max(1, sol['distance']))
            same_pitch = abs(primary['aim'][1] - sol['aim'][1]) <= math.atan2(sol['height'] * .35, max(1, sol['distance']))
            on_target = same_yaw and same_pitch and aligned(me, sol, self.cfg)
            if kind == 'gun':
                action['fire'][i] = bool(on_target and weapon.get('ammo', 0) > 0 and weapon.get('reload', 0) <= 0
                                         and weapon['cooldown'] <= .1 and length([sol['target'][j] - sol['origin'][j] for j in range(3)]) < stats['range']
                                         and self.clear(sol))
            elif kind == 'laser':
                if weapon['cooldown'] > .1:
                    continue
                charge = weapon.get('charge', 0)
                if charge > 0:
                    frac = stats.get('min_frac', .35) + (1 - stats.get('min_frac', .35)) * min(1, charge / stats['charge'])
                    damage = stats['damage'] * frac * (1 + self.cfg['cubes'].get('damage_per_cube', .04) * min(me['cubes'], self.cfg['cubes'].get('stat_cap', 10)))
                    if 'mortar' in target.get('weapons', []):
                        damage *= 1 + stats.get('cookoff', .6)
                    ready = charge >= stats['charge'] - .01 or (charge >= stats.get('min_charge', .15) and target.get('hp', 1e9) < damage * .8)
                    action['fire'][i] = not (ready and on_target and self.clear(sol))
                elif i == selected and sol['distance'] < stats['range'] and self.clear(sol):
                    action['fire'][i] = True
            elif kind == 'mortar':
                action['fire'][i] = bool(i == selected and on_target and weapon['cooldown'] <= .1
                                         and sol['distance'] > max(24, stats['splash_radius'] * 2 + 5)
                                         and self.arc_clear(sol, stats, deadline))
                if action['fire'][i]:
                    self.lock = None
            elif kind == 'lightning':
                # Cone is specified about yaw; use half its configured width conservatively.
                yaw_error = max(0, abs(wrap(sol['aim'][0] - me['aim'][0])) - me['stats']['turn_rate'] / self.cfg['match']['tick_hz'])
                common_error = abs(wrap(primary['aim'][0] - sol['aim'][0]))
                tolerance = math.radians(stats['cone_deg']) * .48
                action['fire'][i] = bool(sol['distance'] < stats['range'] - .5 and yaw_error < tolerance
                                         and common_error < tolerance and weapon['cooldown'] <= .1
                                         and me['energy'] > stats['energy'] + (4 if distance < 25 else 15) and self.clear(sol))
        return action
