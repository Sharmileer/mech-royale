"""Oval Sentinel: a placement-oriented mechfight/1 bot.

Uses public geometry and observed state only. No network, third-party packages,
learned weights, runtime writes, or assumptions about private practice seeds.
"""
from __future__ import annotations
from collections import deque
import math
import random
import sys
import time

import mechfight as mf
from arena_map import ArenaMap, Navigator, clamp
from combat import Combat, dimensions, wrap


def unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 1e-8 else (0.0, 0.0)


def future_zone(zone, now, after):
    """obs center/radius are CURRENT, not the phase's original values."""
    start = max(now, zone['shrink_start'])
    end = zone['shrink_end']
    if end <= start:
        return list(zone['next_center']), zone['next_radius']
    fraction = clamp((now + after - start) / (end - start), 0, 1)
    center = [zone['center'][i] + fraction * (zone['next_center'][i] - zone['center'][i]) for i in range(2)]
    radius = zone['radius'] + fraction * (zone['next_radius'] - zone['radius'])
    return center, radius


class Bot:
    name = 'Oval Sentinel'

    def __init__(self, hello):
        self.cfg = hello['config']
        self.rng = random.Random(hello.get('seed', 0))
        self.orbit = self.rng.choice([-1, 1])
        self.scan_phase = self.rng.random() * math.tau
        self.world = None
        try:
            self.world = ArenaMap(hello['map']['dir'], self.cfg)
        except (OSError, ValueError, KeyError) as exc:
            # Emergency fallback still obeys the protocol; stderr is visible in check failures.
            print(f'map unavailable; navigation degraded: {exc}', file=sys.stderr, flush=True)
        self.nav = Navigator(self.world) if self.world else None
        self.combat = Combat(self.cfg, self.world)
        self.memory = {k: {} for k in ('cubes', 'pickups', 'crates')}
        self.goal = None
        self.goal_kind = 'rotate'
        self.goal_id = None
        self.goal_at = -100.0
        self.recovering = False
        self.last_damage = -100.0
        self.last_dealt = -100.0
        self.last_bearing = None
        self.last_target = None
        self.history = deque()
        self.last_move = [0.0, 0.0]
        self.evade_until = 0.0
        self.evade_angle = 0.0
        self.last_escape = -100.0
        self.boosting = False
        self.stats = {'decisions': 0, 'max_decision_ms': 0.0}

    def _remember(self, obs):
        now, me = obs['t'], obs['self']
        for kind, records in self.memory.items():
            visible = {p['id'] for p in obs.get(kind, [])}
            for p in obs.get(kind, []):
                records[p['id']] = (p, now)
            for ident, (p, seen) in list(records.items()):
                # Cull only close items we can actually see; memory survives turning away.
                near = math.dist(p['pos'][:2], me['pos'][:2]) < 8 and abs(p['pos'][2] - me['pos'][2]) < 4
                clear = near and (not self.world or self.world.line_clear(
                    [*me['pos'][:2], me['pos'][2] + me['stats']['eye_height']],
                    [*p['pos'][:2], p['pos'][2] + 1]))
                if now - seen > 70 or (ident not in visible and clear):
                    del records[ident]
        for event in obs.get('events', []):
            if event['type'] == 'damaged':
                self.last_damage = now
                if event.get('bearing') is not None:
                    self.last_bearing = event['bearing']
            elif event['type'] == 'hit':
                self.last_dealt = now
            elif event['type'] == 'took':
                self.memory['pickups'].pop(event['id'], None)
            elif event['type'] == 'take_failed' and event.get('reason') == 'gone':
                self.memory['pickups'].pop(event['id'], None)
        self.history.append((now, me['pos'][:2]))
        while self.history and self.history[0][0] < now - 1.3:
            self.history.popleft()

    def _upgrade(self, obs):
        offer, me = obs.get('offer'), obs['self']
        if not offer:
            return None
        # Mobility and sustain are valuable in both squad practice and free-for-all.
        scores = {'servos': 100, 'nanites': 97, 'armor': 94, 'plating': 91,
                  'jets': 86, 'reactor': 84, 'gun_mag': 80, 'gun_velocity': 79,
                  'gun_damage': 78, 'laser_damage': 77, 'laser_charge': 76,
                  'gun_rate': 73, 'capacitor': 70, 'lightning_range': 83,
                  'lightning_efficiency': 78, 'mortar_splash': 74,
                  'mortar_reload': 72, 'mortar_velocity': 71,
                  'gyros': 65, 'sensors': 64, 'lightning_chain': 59, 'laser_pierce': 54}
        if me['hp'] < .5 * me['max_hp']:
            scores['plating'] += 10
            scores['nanites'] += 6
        if obs['zone']['radius'] < 80:
            scores['armor'] += 12
            scores['plating'] += 11
            scores['gun_damage'] += 13
            scores['servos'] -= 9
        return max(range(len(offer['choices'])), key=lambda i: scores.get(offer['choices'][i]['id'], 50))

    def _weapon_value(self, kind, obs, enemies):
        values = {'gun': 72, 'laser': 83, 'mortar': 70, 'lightning': 76}
        if obs['zone']['radius'] < 85:
            values['lightning'] += 20
            values['laser'] -= 12
        nearby = enemies[:3]
        if any('laser' in e.get('weapons', []) and math.dist(e['pos'][:2], obs['self']['pos'][:2]) < 90 for e in nearby):
            values['lightning'] += 10
            values['mortar'] -= 15
        if any('mortar' in e.get('weapons', []) for e in nearby):
            values['laser'] += 8
        return values.get(kind, 0)

    def _pickup(self, obs, enemies):
        me = obs['self']
        held = [w['kind'] for w in me['weapons'] if w]
        available = []
        for p in obs.get('pickups', []):
            d = math.dist(p['pos'][:2], me['pos'][:2])
            if d <= self.cfg['loot']['pickup_radius'] - .15 and abs(p['pos'][2] - me['pos'][2]) <= 3.8 and p['kind'] not in held:
                available.append(p)
        if not available:
            return {}
        p = max(available, key=lambda x: self._weapon_value(x['kind'], obs, enemies))
        if None in me['weapons']:
            return {'take': p['id']}
        # Keep the reliable, energy-free gun. Swapping a second weapon must be worthwhile.
        for i, weapon in enumerate(me['weapons']):
            if weapon['kind'] == 'gun' or weapon.get('charge', 0) > 0:
                continue
            if self._weapon_value(p['kind'], obs, enemies) > self._weapon_value(weapon['kind'], obs, enemies) + 12:
                return {'take': p['id'], 'drop': i}
        return {}

    def _zone_cost(self, point, obs, arrival=0):
        zone, now = obs['zone'], obs['t']
        margin = min(14, max(0, zone['next_radius'] * .13))
        center, radius = future_zone(zone, now, max(7, arrival + 5))
        current_out = max(0, math.dist(point[:2], zone['center']) - max(0, zone['radius'] - margin))
        future_out = max(0, math.dist(point[:2], center) - max(0, radius - margin))
        return current_out * (3 + zone['dps'] / 35) + future_out * (2 + zone['dps'] / 65)

    def _exposure(self, point, obs, enemies, deadline):
        if not enemies:
            return 0.0
        ground = self.world.height(*point[:2]) if self.world else obs['self']['pos'][2]
        own_top = [point[0], point[1], ground + obs['self']['stats']['height'] * .65]
        result = 0.0
        for enemy in enemies[:4]:
            if time.perf_counter() > deadline:
                break
            d = math.dist(point[:2], enemy['pos'][:2])
            if d > 260:
                continue
            _, eh = dimensions(enemy, self.cfg)
            source = [enemy['pos'][0], enemy['pos'][1], enemy['pos'][2] + eh * .8]
            exposed = not self.world or self.world.line_clear(source, own_top)
            danger = (1 + min(enemy.get('cubes', 0), 10) * .05) * (24 + 1500 / (d + 25))
            if 'lightning' in enemy.get('weapons', []) and d < 65:
                danger += (65 - d) * 1.1
            if enemy.get('charging'):
                danger *= 1.2
            result += danger * (1 if exposed else .12)
            result += max(0, 30 - d) * 1.6
        return result

    def _safe_goal(self, obs, enemies, deadline):
        me, now, zone = obs['self'], obs['t'], obs['zone']
        pos = me['pos']
        center, radius = future_zone(zone, now, 16)
        candidates = [pos[:2], center, zone['next_center']]
        # Stay on a reachable side of the next circle rather than always crossing its center.
        ux, uy = unit(pos[0] - zone['next_center'][0], pos[1] - zone['next_center'][1])
        r = min(zone['next_radius'] * .55, max(0, zone['next_radius'] - 25))
        candidates.append([zone['next_center'][0] + ux * r, zone['next_center'][1] + uy * r])
        step = min(42, max(12, radius * .35))
        for i in range(12):
            angle = self.scan_phase + i * math.tau / 12
            candidates.append([pos[0] + math.cos(angle) * step, pos[1] + math.sin(angle) * step])
        # Corners provide candidates on the sheltered side of local buildings.
        if self.world and enemies:
            prisms = sorted(self.world.near_prisms(pos[0], pos[1], 38),
                            key=lambda p: math.hypot((p.x0 + p.x1) * .5 - pos[0], (p.y0 + p.y1) * .5 - pos[1]))[:3]
            padding = me['stats']['radius'] + 5
            for prism in prisms:
                candidates.extend([[prism.x0 - padding, prism.y0 - padding], [prism.x1 + padding, prism.y0 - padding],
                                   [prism.x0 - padding, prism.y1 + padding], [prism.x1 + padding, prism.y1 + padding]])
        best, best_score = pos[:2], -float('inf')
        for point in candidates:
            if time.perf_counter() > deadline:
                break
            if self.world:
                cell = self.world.nearest_free(point[0], point[1], me['stats']['radius'], max_cells=4)
                if cell is None:
                    continue
                point = self.world.xy(cell)
            distance = math.dist(pos[:2], point)
            score = -self._zone_cost(point, obs, distance / max(1, me['stats']['walk_speed']))
            score -= distance * (.11 if enemies else .07)
            risk = self._exposure(point, obs, enemies, deadline)
            score -= risk * (1.6 if self.recovering or len(enemies) >= 2 else .6)
            if enemies and not self.recovering and len(enemies) == 1:
                enemy = enemies[0]
                desired = 95 if 'lightning' in enemy.get('weapons', []) else 70
                if any(w and w['kind'] == 'laser' for w in me['weapons']):
                    desired = max(desired, 120)
                score -= abs(math.dist(point, enemy['pos'][:2]) - desired) * .6
            if not enemies:
                score -= math.dist(point, candidates[3]) * .18
                # A little cover is useful even before a shooter is observed.
                if self.world:
                    clearance = self.world.clearance[self.world.cell(*point)]
                    score += max(0, 11 - abs(clearance - me['stats']['radius'] - 5)) * .35
            if self.goal and math.dist(point, self.goal[:2]) < 12:
                score += 3.0
            if score > best_score:
                best, best_score = point, score
        return list(best)

    def _choose_goal(self, obs, enemies, deadline):
        me, pos = obs['self'], obs['self']['pos']
        # Immediate circle danger takes priority over discretionary loot.
        if not me['in_zone'] or self._zone_cost(pos, obs) > 45:
            point = self._safe_goal(obs, enemies, deadline)
            return point, 'rotate', None
        nearest = math.dist(pos[:2], enemies[0]['pos'][:2]) if enemies else 999
        if self.recovering or nearest < 135:
            return self._safe_goal(obs, enemies, deadline), 'recover' if self.recovering else 'kite', None
        held = [w['kind'] for w in me['weapons'] if w]
        choices = []
        for kind, records in self.memory.items():
            for ident, (item, seen) in records.items():
                point = item['pos']
                distance = math.dist(pos[:2], point[:2])
                if distance > 170 or abs(point[2] - pos[2]) > 5:
                    continue
                if kind == 'cubes':
                    value = 130 if me['cubes'] < 10 else 75
                elif kind == 'pickups':
                    if item['kind'] in held:
                        continue
                    value = self._weapon_value(item['kind'], obs, enemies) + (70 if len(held) < 2 else -45)
                    if value < 50:
                        continue
                else:
                    value = 78 if me['cubes'] < 6 else 50
                    if me['hp'] < .85 * me['max_hp'] or nearest < 180:
                        continue
                utility = value - distance * .65 - self._zone_cost(point, obs, distance / me['stats']['walk_speed'])
                if any(math.dist(point[:2], e['pos'][:2]) < 100 for e in enemies):
                    utility -= 90
                if ident == self.goal_id:
                    utility += 9
                choices.append((utility, list(point[:2]), kind, ident))
        for drop in obs.get('drops', []):
            distance = math.dist(pos[:2], drop['pos'][:2])
            if distance > 180 or drop['lands_in'] > distance / me['stats']['walk_speed'] + 7:
                continue
            if any(math.dist(drop['pos'][:2], e['pos'][:2]) < 150 for e in enemies):
                continue
            utility = 135 + 65 * (1 - me['hp'] / me['max_hp']) - distance * .7
            utility -= self._zone_cost(drop['pos'], obs, distance / me['stats']['walk_speed'] + drop['capture_s'])
            choices.append((utility, drop['pos'][:2], 'drop', drop['id']))
        if choices:
            best = max(choices, key=lambda c: c[0])
            if best[0] > 12:
                return best[1:]
        return self._safe_goal(obs, enemies, deadline), 'rotate', None

    def _target(self, obs, enemies, deadline):
        me = obs['self']
        muzzle = [*me['pos'][:2], me['pos'][2] + me['stats']['muzzle_height']]
        candidates = []
        for enemy in enemies[:6]:
            if time.perf_counter() > deadline:
                break
            d = math.dist(me['pos'][:2], enemy['pos'][:2])
            _, height = dimensions(enemy, self.cfg)
            target = [*enemy['pos'][:2], enemy['pos'][2] + height * .5]
            clear = not self.world or self.world.line_clear(muzzle, target)
            score = 130 - d * .28 - enemy.get('hp', 1400) * .025
            if not clear:
                score -= 80
            if enemy['id'] == self.last_target:
                score += 9
            if d < 50:
                score += 25
            candidates.append((score, enemy))
        if candidates:
            chosen = max(candidates, key=lambda x: x[0])[1]
            self.last_target = chosen['id']
            return chosen
        if self.recovering:
            return None
        # Farm only close, visible loot so we can collect it, not broadcast wastefully.
        for crate in obs.get('crates', []):
            d = math.dist(me['pos'][:2], crate['pos'][:2])
            if 12 <= d < 80 and (self.goal_kind == 'crates' and crate['id'] == self.goal_id):
                return {**crate, 'kind': 'crate', 'vel': [0, 0, 0]}
        for npc in obs.get('mechs', []):
            d = math.dist(me['pos'][:2], npc['pos'][:2])
            if npc['kind'] == 'npc' and d < 65 and me['hp'] > me['max_hp'] * .8:
                return npc
        return None

    def _move(self, obs, enemies, target, deadline):
        me, now, pos = obs['self'], obs['t'], obs['self']['pos']
        radius, height = me['stats']['radius'], me['stats']['height']
        goal = self.goal or obs['zone']['next_center']
        waypoint = self.nav.waypoint(pos, goal, radius, height, now, min(deadline, time.perf_counter() + .004)) if self.nav else goal
        gx, gy = unit(waypoint[0] - pos[0], waypoint[1] - pos[1])
        near = math.dist(pos[:2], enemies[0]['pos'][:2]) if enemies else 999
        danger = near < 140 or now - self.last_damage < 2
        # Capture requires standing inside the ring; don't orbit out of it.
        capture = self.goal_kind == 'drop' and math.dist(pos[:2], goal) < 4 and not danger
        arrived = math.dist(pos[:2], goal) < 3
        sheltered = False
        if arrived and self.recovering and self.world and now - self.last_damage > 1:
            own_top = [pos[0], pos[1], pos[2] + height * .7]
            sheltered = all(not self.world.line_clear(
                [e['pos'][0], e['pos'][1], e['pos'][2] + dimensions(e, self.cfg)[1] * .8], own_top)
                for e in enemies[:4])
        if capture or (arrived and (not danger or sheltered)):
            return {'move': [0.0, 0.0], 'boost': False, 'jump': False}
        if self.goal_kind == 'crates' and target and target.get('kind') == 'crate' and math.dist(pos[:2], target['pos'][:2]) < 20:
            return {'move': [0.0, 0.0], 'boost': False, 'jump': False}
        if danger and enemies and not self.recovering and self.goal_kind != 'rotate':
            ex, ey = unit(enemies[0]['pos'][0] - pos[0], enemies[0]['pos'][1] - pos[1])
            # Change strafe direction slowly enough to avoid losing speed to acceleration.
            sign = self.orbit * (1 if int((now + self.scan_phase) / 2.3) % 2 == 0 else -1)
            gx, gy = unit(gx - ey * sign * .9, gy + ex * sign * .9)
        # Detect a blocked route from actual motion, not velocity alone.
        stuck = (len(self.history) > 7 and now - self.history[0][0] > .8
                 and math.dist(self.history[0][1], pos[:2]) < 1.2
                 and math.hypot(*self.last_move) > .7)
        jump = False
        if stuck and now - self.last_escape > 1.5:
            self.last_escape = now
            self.evade_until = now + 1.0
            self.evade_angle = math.atan2(gy, gx) + self.orbit * math.pi * .65
            if self.nav:
                self.nav.invalidate()
            jump = me['grounded'] and me['energy'] > 45
        if now < self.evade_until:
            gx, gy = math.cos(self.evade_angle), math.sin(self.evade_angle)
        # Burst boost, then recharge with hysteresis instead of spending the last unit every tick.
        need_boost = not me['in_zone'] or self._zone_cost(pos, obs) > 40 or (danger and (self.recovering or near < 70))
        if self.boosting:
            self.boosting = need_boost and me['energy'] > 23
        else:
            self.boosting = need_boost and me['energy'] > 62
        boost = self.boosting and me['grounded']
        if jump or (not me['grounded'] and now < self.evade_until and me['energy'] > 30):
            boost = True
        # Short-horizon motion candidates with collision, bullet and zone penalties.
        speed = me['stats']['boost_speed'] if boost else me['stats']['walk_speed']
        base = math.atan2(gy, gx)
        choices = [(math.cos(base + offset), math.sin(base + offset)) for offset in (0, .4, -.4, .85, -.85, 1.4, -1.4, math.pi)]
        best, best_score = (0.0, 0.0), -float('inf')
        baseline_zone = self._zone_cost(pos, obs)
        for dx, dy in choices:
            if time.perf_counter() > deadline:
                break
            # Account for inertia during the first tenth of a second.
            px = pos[0] + me['vel'][0] * .10 + dx * speed * .36
            py = pos[1] + me['vel'][1] * .10 + dy * speed * .36
            if self.world and not self.world.can_move(pos, (px, py), radius, height):
                continue
            score = (dx * gx + dy * gy) * 8 + (dx * self.last_move[0] + dy * self.last_move[1]) * .6
            score -= max(-15, self._zone_cost([px, py], obs) - baseline_zone) * .6
            if self.world:
                clear = self.world.clearance[self.world.cell(px, py)]
                score -= max(0, radius + 2 - clear) * .8
            for enemy in enemies[:3]:
                before = math.dist(pos[:2], enemy['pos'][:2])
                after = math.dist((px, py), enemy['pos'][:2])
                safe = 65 if 'lightning' in enemy.get('weapons', []) else 35
                if before < safe:
                    score += (after - before) * 1.4
                if enemy.get('charging'):
                    ex, ey = px - enemy['pos'][0], py - enemy['pos'][1]
                    yaw = enemy['aim'][0]
                    forward = ex * math.cos(yaw) + ey * math.sin(yaw)
                    sideways = abs(-ex * math.sin(yaw) + ey * math.cos(yaw))
                    if forward > 0:
                        score -= max(0, radius + 3 - sideways) * 3.0
            for bullet in obs.get('projectiles', [])[:36]:
                bx, by, bz = bullet['pos']
                vx, vy, vz = bullet['vel']
                rx, ry = bx - pos[0], by - pos[1]
                rvx, rvy = vx - dx * speed, vy - dy * speed
                closest = clamp(-(rx * rvx + ry * rvy) / max(1, rvx * rvx + rvy * rvy), 0, .65)
                separation = math.hypot(rx + rvx * closest, ry + rvy * closest)
                z = bz + vz * closest
                if bullet['kind'] == 'mortar':
                    z -= .5 * self.cfg['weapons']['mortar']['gravity'] * closest * closest
                    threshold = radius + self.cfg['weapons']['mortar']['splash_radius']
                else:
                    threshold = radius + .8
                if pos[2] - 2 < z < pos[2] + height + 2 and closest > .01:
                    score -= max(0, threshold - separation) * (5 if bullet['kind'] == 'mortar' else 8)
            if score > best_score:
                best, best_score = (dx, dy), score
        if best_score == -float('inf'):
            boost = False
            if me['grounded'] and me['energy'] > 55 and now - self.last_escape > 1.5:
                jump, boost = True, True
                self.last_escape = now
                self.evade_until = now + 1.0
                self.evade_angle = base
        return {'move': list(best), 'boost': bool(boost), 'jump': bool(jump)}

    def act(self, obs):
        started = time.perf_counter()
        # Leave substantial wall-clock margin for parsing, scheduling and transport.
        decision_budget = min(.020, self.cfg['bot']['tick_budget_ms'] / 1000 * .40)
        deadline = started + decision_budget
        me, now = obs['self'], obs['t']
        self._remember(obs)
        enemies = sorted((e for e in obs.get('mechs', []) if e['kind'] == 'mech'),
                         key=lambda e: math.dist(e['pos'][:2], me['pos'][:2]))
        ratio = me['hp'] / max(1, me['max_hp'])
        threats = sum(math.dist(e['pos'][:2], me['pos'][:2]) < 145 for e in enemies)
        if ratio < .46 or (threats >= 2 and ratio < .72):
            self.recovering = True
        if ratio > .9 or (obs['zone']['radius'] < 22 and obs['alive'] <= 3):
            self.recovering = False
        goal_exists = self.goal_id is None or self.goal_kind == 'drop' or self.goal_id in self.memory.get(self.goal_kind, {})
        if self.goal is None or now - self.goal_at > .9 or not goal_exists:
            self.goal, self.goal_kind, self.goal_id = self._choose_goal(obs, enemies, started + decision_budget * .46)
            self.goal_at = now
        target = self._target(obs, enemies, started + decision_budget * .62)
        action = {'tick': obs['tick']}
        action.update(self._pickup(obs, enemies))
        upgrade = self._upgrade(obs)
        if upgrade is not None:
            action['pick'] = upgrade
        action.update(self.combat.act(obs, target, self.recovering, started + decision_budget * .77))
        action.update(self._move(obs, enemies, target, deadline))
        if 'aim' not in action:
            if now - self.last_damage < 2 and self.last_bearing is not None:
                yaw = self.last_bearing
            else:
                # Scan the horizon in calm periods; useful items beyond 45 m need FOV.
                useful_sounds = [s for s in obs.get('sounds', []) if s['dist'] != 'far' and s['kind'] not in ('death', 'drop', 'crate')]
                yaw = useful_sounds[0]['bearing'] if useful_sounds else wrap(me['aim'][0] + .18 * self.orbit)
            action['aim'] = [yaw, 0.0]
        for weapon in me['weapons']:
            if weapon and weapon['kind'] == 'gun' and weapon.get('reload', 0) == 0:
                ammo, mag = weapon.get('ammo', 0), weapon['stats']['mag']
                if ammo == 0 or (ammo < mag * .65 and (target is None or self.recovering)):
                    action['reload'] = True
        self.last_move = action['move']
        self.stats['decisions'] += 1
        self.stats['max_decision_ms'] = max(self.stats['max_decision_ms'], (time.perf_counter() - started) * 1000)
        return action


if __name__ == '__main__':
    mf.run(Bot)
